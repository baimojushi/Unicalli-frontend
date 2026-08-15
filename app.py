# -*- coding: utf-8 -*-
"""UniCalli · persistent horizontal digital scroll workspace.

The scroll DOM is mounted once. Python emits small segment events, while the
browser updates only the active segment. Completed images remain server-side.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import gradio as gr
from PIL import Image
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from unicalli_core import (
    SYNTHETIC_AUTHOR,
    GenerationRequest,
    GeneratorService,
    SegmentTask,
    compose_seamless_scroll,
    font_choices_for_author,
    load_project_data,
    sanitize_han_text,
    split_text_into_segments,
)
from unicalli_ui import (
    author_tag_value,
    favorite_button_label,
    image_to_data_uri,
    load_asset_text,
    normalize_preferences,
    ordered_author_choices,
    render_author_preference_summary,
    render_draft_strip,
    render_stage_shell,
    segment_payloads,
)


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / ".unicalli_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

PROJECT = load_project_data(BASE_DIR)
AUTHOR_LIST = PROJECT.author_list
GENERATOR_SERVICE = GeneratorService(BASE_DIR)
DESKTOP_CSS = load_asset_text("unicalli_ui.css")
MOBILE_CSS = load_asset_text("unicalli_mobile.css")
CSS = f"{DESKTOP_CSS}\n\n{MOBILE_CSS}"
INTERACTIONS_JS = load_asset_text("unicalli_ui.js")
MOBILE_UI_JS = load_asset_text("unicalli_mobile.js")
BOOTSTRAP_JS = f"{INTERACTIONS_JS}\n\n{MOBILE_UI_JS}"

GRADIO_MAJOR = int(gr.__version__.split(".", 1)[0])
BLOCKS_STYLE_KWARGS = {"css": CSS} if GRADIO_MAJOR < 6 else {}

INITIAL_AUTHOR = (
    "黄庭坚"
    if "黄庭坚" in PROJECT.author_fonts
    else (AUTHOR_LIST[0] if AUTHOR_LIST else SYNTHETIC_AUTHOR)
)
INITIAL_TEXT = "山川异域风月同天"
INITIAL_FONT_CHOICES = font_choices_for_author(INITIAL_AUTHOR, PROJECT.author_fonts)
DEFAULT_PREFERENCES: Dict[str, Any] = {"favorites": [], "tags": {}}

SESSION_TTL_SECONDS = 2 * 60 * 60
MAX_SCROLL_SESSIONS = 32


@dataclass
class ScrollSession:
    session_id: str
    segments: List[SegmentTask]
    images: List[Optional[Image.Image]]
    original_request: GenerationRequest
    base_seed: int
    segment_seeds: List[Optional[int]]
    background_mode: str
    busy: bool = True
    revision: int = 0
    updated_at: float = field(default_factory=time.time)


SESSION_STORE: Dict[str, ScrollSession] = {}
SESSION_LOCK = threading.RLock()


def _cleanup_sessions() -> None:
    now = time.time()
    with SESSION_LOCK:
        expired = [
            session_id
            for session_id, session in SESSION_STORE.items()
            if not session.busy and now - session.updated_at > SESSION_TTL_SECONDS
        ]
        for session_id in expired:
            SESSION_STORE.pop(session_id, None)

        if len(SESSION_STORE) <= MAX_SCROLL_SESSIONS:
            return

        candidates = sorted(
            (session for session in SESSION_STORE.values() if not session.busy),
            key=lambda item: item.updated_at,
        )
        for session in candidates[: max(0, len(SESSION_STORE) - MAX_SCROLL_SESSIONS)]:
            SESSION_STORE.pop(session.session_id, None)


def _new_session(
    request: GenerationRequest,
    segments: List[SegmentTask],
    background_mode: str,
) -> ScrollSession:
    _cleanup_sessions()
    session = ScrollSession(
        session_id=uuid.uuid4().hex,
        segments=segments,
        images=[None] * len(segments),
        original_request=request,
        base_seed=int(request.seed),
        segment_seeds=[None] * len(segments),
        background_mode=background_mode,
    )
    with SESSION_LOCK:
        SESSION_STORE[session.session_id] = session
    return session


def _get_session(session_id: Any) -> ScrollSession:
    key = str(session_id or "").strip()
    with SESSION_LOCK:
        session = SESSION_STORE.get(key)
        if session is None:
            raise gr.Error("这幅长卷已过期，请另题一卷。")
        session.updated_at = time.time()
        return session


def _completed_indices(session: ScrollSession) -> List[int]:
    return [index for index, image in enumerate(session.images) if image is not None]


def _seed_summary(session: ScrollSession) -> str:
    overrides: List[str] = []
    for index, seed_value in enumerate(session.segment_seeds):
        default_seed = int(session.base_seed) + index
        if seed_value is None or int(seed_value) == default_seed:
            continue
        overrides.append(f"{index + 1:02d}:{int(seed_value)}")
    suffix = " · 各段种子依次递增"
    if overrides:
        return f"基础 Seed {session.base_seed}{suffix} · 重写段 {' / '.join(overrides)}"
    return f"基础 Seed {session.base_seed}{suffix}"


def _next_event(session: ScrollSession, kind: str, **payload: Any) -> Dict[str, Any]:
    with SESSION_LOCK:
        session.revision += 1
        session.updated_at = time.time()
        return {
            "kind": kind,
            "session_id": session.session_id,
            "revision": session.revision,
            **payload,
        }


def _export_path(session: ScrollSession, image: Image.Image, seed: int) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    path = EXPORT_DIR / (
        f"unicalli-{session.session_id[:8]}-r{session.revision}-"
        f"{timestamp}-seed-{seed}.png"
    )
    image.save(path, format="PNG")
    return str(path)


def _export_session(session: ScrollSession, seed: int) -> Optional[str]:
    if not session.images or any(image is None for image in session.images):
        return None
    completed = [image for image in session.images if image is not None]
    scroll = compose_seamless_scroll(completed, session.background_mode)
    return _export_path(session, scroll, seed) if scroll is not None else None


def _download_update(path: Optional[str]):
    return gr.DownloadButton(value=path, visible=bool(path))


def preview_text(text: str) -> str:
    return render_draft_strip(split_text_into_segments(sanitize_han_text(text)))


def _author_component_updates(author: str, preferences: Any):
    choices = font_choices_for_author(author, PROJECT.author_fonts)
    is_synthetic = author == SYNTHETIC_AUTHOR
    return (
        gr.Dropdown(choices=choices, value=choices[0] if choices else None),
        gr.Button(
            value="合成风格" if is_synthetic else favorite_button_label(author, preferences),
            interactive=not is_synthetic,
        ),
        gr.Textbox(
            value="" if is_synthetic else author_tag_value(author, preferences),
            interactive=not is_synthetic,
        ),
    )


def author_change(author: str, preferences: Any):
    return _author_component_updates(author, preferences)


def filter_author_controls(
    favorites_only: bool,
    preferences: Any,
    current_author: str,
):
    choices = ordered_author_choices(AUTHOR_LIST, preferences, favorites_only)
    selected = current_author if current_author in choices else choices[0]
    font_update, favorite_update, tag_update = _author_component_updates(
        selected, preferences
    )
    return (
        gr.Dropdown(choices=choices, value=selected),
        font_update,
        favorite_update,
        tag_update,
    )


def toggle_author_favorite(
    author: str,
    preferences: Any,
    favorites_only: bool,
):
    prefs = normalize_preferences(preferences)

    if author != SYNTHETIC_AUTHOR:
        favorites = list(prefs["favorites"])
        if author in favorites:
            favorites.remove(author)
        else:
            favorites.insert(0, author)
        prefs["favorites"] = favorites

    choices = ordered_author_choices(AUTHOR_LIST, prefs, favorites_only)
    selected = author if author in choices else choices[0]
    font_update, favorite_update, tag_update = _author_component_updates(
        selected, prefs
    )
    return (
        prefs,
        render_author_preference_summary(prefs),
        gr.Dropdown(choices=choices, value=selected),
        font_update,
        favorite_update,
        tag_update,
    )


def save_author_tag(author: str, tag: str, preferences: Any):
    prefs = normalize_preferences(preferences)
    if author == SYNTHETIC_AUTHOR:
        return (
            prefs,
            render_author_preference_summary(prefs),
            gr.Textbox(value="", interactive=False),
        )

    clean_tag = (tag or "").strip()[:18]
    tags = dict(prefs["tags"])
    if clean_tag:
        tags[author] = clean_tag
    else:
        tags.pop(author, None)
    prefs["tags"] = tags
    return (
        prefs,
        render_author_preference_summary(prefs),
        gr.Textbox(value=clean_tag),
    )


def initialize_preferences(preferences: Any, current_author: str):
    prefs = normalize_preferences(preferences)
    choices = ordered_author_choices(AUTHOR_LIST, prefs, False)
    selected = current_author if current_author in choices else choices[0]
    _, favorite_update, tag_update = _author_component_updates(selected, prefs)
    return (
        gr.Dropdown(choices=choices, value=selected),
        favorite_update,
        tag_update,
        render_author_preference_summary(prefs),
    )


def generation_ui_stream(
    text: str,
    author_dropdown: str,
    font_style: str,
    num_steps: int,
    seed: int,
    random_seed: bool,
    quant_mode: str,
    background_mode: str,
) -> Generator[Tuple[Any, Any, Any, Any, Any, Any], None, None]:
    clean_text = sanitize_han_text(text)
    segments = split_text_into_segments(clean_text)
    if not segments:
        raise gr.Error("请先录入汉字。")

    request = GenerationRequest(
        text=clean_text,
        author=None if author_dropdown == SYNTHETIC_AUTHOR else author_dropdown,
        font_style=font_style,
        num_steps=int(num_steps),
        seed=int(seed),
        random_seed=bool(random_seed),
        quant_mode=quant_mode,
    )
    session = _new_session(request, segments, background_mode)
    active_index: Optional[int] = None

    yield (
        _next_event(
            session,
            "reset",
            segments=segment_payloads(segments),
        ),
        render_draft_strip(segments),
        "起卷中。",
        f"共 {len(segments)} 段",
        _download_update(None),
        session.session_id,
    )

    try:
        for event in GENERATOR_SERVICE.stream(request):
            if event.seed is not None:
                session.base_seed = int(event.seed)

            if event.type == "task_started":
                yield (
                    _next_event(
                        session,
                        "task_started",
                        seed=session.base_seed,
                        total_steps=event.total_steps,
                    ),
                    gr.skip(),
                    event.message,
                    _seed_summary(session),
                    gr.skip(),
                    gr.skip(),
                )

            elif event.type == "segment_started" and event.segment_index is not None:
                active_index = int(event.segment_index)
                yield (
                    _next_event(
                        session,
                        "segment_started",
                        index=active_index,
                        total_steps=event.total_steps,
                    ),
                    render_draft_strip(
                        segments,
                        active_index=active_index,
                        completed_indices=_completed_indices(session),
                    ),
                    event.message,
                    _seed_summary(session),
                    gr.skip(),
                    gr.skip(),
                )

            elif event.type == "preview" and event.segment_index is not None:
                active_index = int(event.segment_index)
                yield (
                    _next_event(
                        session,
                        "preview",
                        index=active_index,
                        image=image_to_data_uri(event.image, quality=72),
                        step=event.step,
                        total_steps=event.total_steps,
                    ),
                    gr.skip(),
                    event.message,
                    _seed_summary(session),
                    gr.skip(),
                    gr.skip(),
                )

            elif event.type == "segment_completed" and event.segment_index is not None:
                if event.image is None:
                    raise RuntimeError("段落完成事件缺少图像。")
                active_index = int(event.segment_index)
                final_image = event.image
                with SESSION_LOCK:
                    session.images[active_index] = final_image
                    session.segment_seeds[active_index] = int(event.seed)

                yield (
                    _next_event(
                        session,
                        "segment_completed",
                        index=active_index,
                        image=image_to_data_uri(final_image, quality=88),
                        seed=session.segment_seeds[active_index],
                    ),
                    render_draft_strip(
                        segments,
                        active_index=None,
                        completed_indices=_completed_indices(session),
                    ),
                    event.message,
                    _seed_summary(session),
                    gr.skip(),
                    gr.skip(),
                )

            elif event.type == "task_completed":
                with SESSION_LOCK:
                    session.busy = False
                export_path = _export_session(session, session.base_seed)
                yield (
                    _next_event(
                        session,
                        "task_completed",
                        index=active_index,
                        seed=session.base_seed,
                    ),
                    render_draft_strip(
                        segments,
                        completed_indices=_completed_indices(session),
                    ),
                    event.message,
                    _seed_summary(session),
                    _download_update(export_path),
                    gr.skip(),
                )

    except Exception as error:
        with SESSION_LOCK:
            session.busy = False
        yield (
            _next_event(
                session,
                "task_error",
                index=active_index,
                message=str(error),
            ),
            render_draft_strip(
                segments,
                active_index=None,
                completed_indices=_completed_indices(session),
            ),
            f"生成已停止 · {error}",
            _seed_summary(session),
            gr.skip(),
            gr.skip(),
        )
        raise gr.Error(str(error)) from error


def reroll_segment_stream(
    session_id: str,
    target_segment: Any,
) -> Generator[Tuple[Any, Any, Any, Any, Any], None, None]:
    session = _get_session(session_id)
    try:
        token = str(target_segment).strip().split(":", 1)[0]
        target_index = int(float(token))
    except Exception as error:
        raise gr.Error("无法识别要重写的段落。") from error

    if target_index < 0 or target_index >= len(session.segments):
        raise gr.Error("目标段落超出范围。")
    if session.images[target_index] is None:
        raise gr.Error("这一段尚未写成，暂不能重写。")

    with SESSION_LOCK:
        if session.busy:
            raise gr.Error("当前仍有生成任务，请稍后再重写。")
        session.busy = True

    source_segment = session.segments[target_index]
    original = session.original_request
    reroll_request = GenerationRequest(
        text=source_segment.model_text,
        author=original.author,
        font_style=original.font_style,
        num_steps=original.num_steps,
        seed=session.base_seed,
        random_seed=True,
        quant_mode=original.quant_mode,
    )
    latest_seed = session.base_seed

    yield (
        _next_event(
            session,
            "reroll_started",
            index=target_index,
            total_steps=reroll_request.num_steps,
        ),
        render_draft_strip(
            session.segments,
            active_index=target_index,
            completed_indices=_completed_indices(session),
        ),
        f"第 {target_index + 1} 段 · 准备重写",
        _seed_summary(session),
        _download_update(None),
    )

    try:
        for event in GENERATOR_SERVICE.stream(reroll_request):
            if event.seed is not None:
                latest_seed = int(event.seed)

            if event.type == "preview":
                yield (
                    _next_event(
                        session,
                        "reroll_preview",
                        index=target_index,
                        image=image_to_data_uri(event.image, quality=72),
                        step=event.step,
                        total_steps=event.total_steps,
                    ),
                    gr.skip(),
                    (
                        f"第 {target_index + 1} 段 · 显墨 "
                        f"{min((event.step or 0) + 1, event.total_steps or 1)}/"
                        f"{event.total_steps or 1}"
                    ),
                    _seed_summary(session),
                    gr.skip(),
                )

            elif event.type == "segment_completed":
                if event.image is None:
                    raise RuntimeError("重写完成事件缺少图像。")
                replacement = event.image

                # Transaction boundary: replace the old image only after success.
                with SESSION_LOCK:
                    session.images[target_index] = replacement
                    session.segment_seeds[target_index] = latest_seed
                    session.busy = False

                export_path = _export_session(session, latest_seed)
                yield (
                    _next_event(
                        session,
                        "reroll_completed",
                        index=target_index,
                        image=image_to_data_uri(replacement, quality=88),
                        seed=latest_seed,
                    ),
                    render_draft_strip(
                        session.segments,
                        completed_indices=_completed_indices(session),
                    ),
                    f"第 {target_index + 1} 段 · 重写完成",
                    _seed_summary(session),
                    _download_update(export_path),
                )
                return

    except Exception as error:
        with SESSION_LOCK:
            session.busy = False

        # The browser receives no replacement image and restores the existing one.
        yield (
            _next_event(
                session,
                "reroll_error",
                index=target_index,
                message=str(error),
            ),
            render_draft_strip(
                session.segments,
                completed_indices=_completed_indices(session),
            ),
            f"第 {target_index + 1} 段 · 重写未成，原图已保留 · {error}",
            _seed_summary(session),
            gr.skip(),
        )
        raise gr.Error(str(error)) from error


def update_background_export(session_id: str, background_mode: str):
    """Keep browser theme and exported file in sync."""
    if not session_id:
        return gr.skip()

    session = _get_session(session_id)
    with SESSION_LOCK:
        session.background_mode = background_mode
    export_path = _export_session(session, session.base_seed)
    return _download_update(export_path) if export_path else gr.skip()


with gr.Blocks(
    title="UniCalli · 数字长卷",
    fill_height=True,
    fill_width=True,
    **BLOCKS_STYLE_KWARGS,
) as demo:
    preferences_state = gr.BrowserState(
        default_value=DEFAULT_PREFERENCES,
        storage_key="unicalli-author-preferences-v2",
    )
    session_id_state = gr.State(value="")

    gr.HTML(
        """
        <div class="unicalli-topbar">
          <div class="topbar-mark">
            <span class="topbar-seal" aria-hidden="true">翰</span>
            <span class="topbar-title">
              <strong>UniCalli</strong>
              <small>数字长卷</small>
            </span>
          </div>
          <div id="run-timer" aria-live="polite">
            <strong>00:00</strong><small>静候</small>
          </div>
          <span class="topbar-balance" aria-hidden="true"></span>
        </div>
        """,
        elem_id="topbar",
        container=False,
    )

    with gr.Column(elem_id="app-shell"):
        stage_html = gr.HTML(
            value=render_stage_shell(),
            elem_id="scroll-stage-host",
            container=False,
            padding=False,
        )

        with gr.Row(elem_id="stage-controls"):
            theme_mode = gr.Radio(
                choices=["纸白", "砚黑"],
                value="纸白",
                label="卷面",
                show_label=False,
                elem_id="theme-mode",
            )
            follow_current_btn = gr.Button(
                "回到当前段", size="sm", elem_id="follow-current-btn"
            )
            fullscreen_btn = gr.Button(
                "全屏展卷", size="sm", elem_id="fullscreen-btn"
            )

        with gr.Column(elem_id="composer-dock"):
            with gr.Row(elem_classes=["composer-main-row"]):
                text_input = gr.Textbox(
                    value=INITIAL_TEXT,
                    placeholder="题写汉字，每五字自动成段",
                    label="题写内容",
                    show_label=False,
                    lines=2,
                    max_lines=4,
                    autofocus=True,
                    elem_id="text-input",
                    scale=6,
                )
                with gr.Column(elem_classes=["selector-stack"], scale=2):
                    author_dropdown = gr.Dropdown(
                        choices=[SYNTHETIC_AUTHOR] + AUTHOR_LIST,
                        value=INITIAL_AUTHOR,
                        label="书家",
                        show_label=False,
                        elem_id="author-dropdown",
                    )
                    font_style = gr.Dropdown(
                        choices=INITIAL_FONT_CHOICES,
                        value=(
                            INITIAL_FONT_CHOICES[0]
                            if INITIAL_FONT_CHOICES
                            else None
                        ),
                        label="书体",
                        show_label=False,
                        elem_id="font-dropdown",
                    )
                generate_btn = gr.Button(
                    "落笔",
                    variant="primary",
                    elem_id="generate-btn",
                    scale=1,
                )

            draft_board = gr.HTML(
                value=preview_text(INITIAL_TEXT),
                elem_id="draft-board",
                container=False,
            )

            with gr.Row(elem_classes=["composer-status-row"]):
                generation_status = gr.Markdown(
                    "长卷待题。", elem_id="status-line"
                )
                edit_again_btn = gr.Button(
                    "另题一卷", size="sm", elem_id="edit-again-btn"
                )
                download_btn = gr.DownloadButton(
                    "导出长卷",
                    value=None,
                    visible=False,
                    size="sm",
                    elem_id="download-scroll-btn",
                )

        with gr.Column(elem_id="side-drawers"):
            with gr.Accordion(
                "书家偏好",
                open=False,
                elem_id="preferences-drawer",
            ):
                with gr.Row(elem_classes=["preference-tools"]):
                    favorite_author_btn = gr.Button(
                        "☆ 收藏",
                        size="sm",
                        elem_id="favorite-author-btn",
                    )
                    author_tag = gr.Textbox(
                        label="书家标注",
                        placeholder="常用、苍劲、待试……",
                        lines=1,
                        max_lines=1,
                        elem_id="author-tag",
                    )
                    save_author_tag_btn = gr.Button(
                        "保存标注",
                        size="sm",
                        elem_id="save-author-tag-btn",
                    )
                    favorites_only = gr.Checkbox(
                        label="只看收藏",
                        value=False,
                        elem_id="favorite-only",
                    )
                preference_summary = gr.HTML(
                    value=render_author_preference_summary(DEFAULT_PREFERENCES),
                    elem_id="preference-summary",
                    container=False,
                )

            with gr.Accordion(
                "生成细节",
                open=False,
                elem_id="advanced-drawer",
            ):
                num_steps = gr.Slider(
                    label="生成步数",
                    minimum=10,
                    maximum=100,
                    value=25,
                    step=1,
                )
                with gr.Row():
                    seed = gr.Number(
                        label="种子", value=42, precision=0
                    )
                    random_seed = gr.Checkbox(
                        label="每次随机", value=False
                    )
                quant_mode = gr.Radio(
                    label="计算精度",
                    choices=[
                        "8-bit（推荐）",
                        "4-bit",
                        "全精度",
                    ],
                    value="8-bit（推荐）",
                )

        seed_info = gr.Textbox(
            value="", interactive=False, elem_id="seed-info"
        )
        event_bus = gr.JSON(
            value={},
            visible=False,
            elem_id="event-bus",
        )
        reroll_target = gr.Textbox(
            value="", lines=1, elem_id="reroll-target"
        )

    gr.HTML(
        value="<span aria-hidden='true'></span>",
        elem_id="js-bootstrap",
        container=False,
        js_on_load=BOOTSTRAP_JS,
    )

    demo.load(
        fn=initialize_preferences,
        inputs=[preferences_state, author_dropdown],
        outputs=[
            author_dropdown,
            favorite_author_btn,
            author_tag,
            preference_summary,
        ],
        queue=False,
    )

    event_bus.change(
        fn=None,
        inputs=[event_bus],
        outputs=None,
        js="(event) => { window.UniCalli?.applyEvent(event); }",
        queue=False,
    )

    text_input.input(
        fn=preview_text,
        inputs=[text_input],
        outputs=[draft_board],
        queue=False,
    )

    author_dropdown.change(
        fn=author_change,
        inputs=[author_dropdown, preferences_state],
        outputs=[font_style, favorite_author_btn, author_tag],
        queue=False,
    )

    favorites_only.change(
        fn=filter_author_controls,
        inputs=[favorites_only, preferences_state, author_dropdown],
        outputs=[
            author_dropdown,
            font_style,
            favorite_author_btn,
            author_tag,
        ],
        queue=False,
    )

    favorite_author_btn.click(
        fn=toggle_author_favorite,
        inputs=[author_dropdown, preferences_state, favorites_only],
        outputs=[
            preferences_state,
            preference_summary,
            author_dropdown,
            font_style,
            favorite_author_btn,
            author_tag,
        ],
        queue=False,
    )

    save_author_tag_btn.click(
        fn=save_author_tag,
        inputs=[author_dropdown, author_tag, preferences_state],
        outputs=[preferences_state, preference_summary, author_tag],
        queue=False,
    )

    theme_mode.change(
        fn=update_background_export,
        inputs=[session_id_state, theme_mode],
        outputs=[download_btn],
        js=(
            "(sessionId, mode) => { "
            "window.UniCalli?.setTheme(mode); "
            "return [sessionId, mode]; }"
        ),
        queue=False,
    )

    follow_current_btn.click(
        fn=None,
        js="() => { window.UniCalli?.followCurrent(); }",
        queue=False,
    )
    fullscreen_btn.click(
        fn=None,
        js="() => { window.UniCalli?.toggleFullscreen(); }",
        queue=False,
    )
    edit_again_btn.click(
        fn=None,
        js="() => { window.UniCalli?.enterEdit(); }",
        queue=False,
    )

    generate_btn.click(
        fn=generation_ui_stream,
        inputs=[
            text_input,
            author_dropdown,
            font_style,
            num_steps,
            seed,
            random_seed,
            quant_mode,
            theme_mode,
        ],
        outputs=[
            event_bus,
            draft_board,
            generation_status,
            seed_info,
            download_btn,
            session_id_state,
        ],
        show_progress="hidden",
        stream_every=0.12,
        js=(
            "(...args) => "
            "window.UniCalli ? window.UniCalli.beforeGenerate(args) : args"
        ),
    )

    reroll_target.change(
        fn=reroll_segment_stream,
        inputs=[session_id_state, reroll_target],
        outputs=[
            event_bus,
            draft_board,
            generation_status,
            seed_info,
            download_btn,
        ],
        show_progress="hidden",
        stream_every=0.12,
    )


demo.queue(max_size=12)


# Gradio 6.x injects CSS during launch(). This project exposes demo.app directly,
# so initialize theme and CSS config once before uvicorn starts.
if GRADIO_MAJOR >= 6:
    from gradio.utils import get_theme

    demo.theme = get_theme(demo.theme)
    demo.css = CSS
    demo.css_paths = []
    demo.head = None
    demo.head_paths = []
    demo._set_html_css_theme_variables()
    demo.config = demo.get_config_file()


_health_app = demo.app


@_health_app.get("/api/health")
def _health():
    return {"status": "ok", "service": "unicalli", "ui": "v4"}


# 子路径部署（Tailscale Funnel /unicalli）下的唯一问题是：
# URL 无尾斜杠时 gradio 相对路径 "./assets/" 会解析到根路径（打到 Funnel
# 根 → Plane）→ 注入脚本把 /unicalli 301 到 /unicalli/ 即可。
# 注意：不能改 api_prefix——gradio 后端已从请求正确推断 root（含 /unicalli），
# 前端 URL = root + api_prefix(/gradio_api) 已正确；注入 api_prefix 会导致
# root + 注入值 双前缀/畸形 URL（实测 404/405）。
# 说明：曾经通过 JS 在客户端清除 gradio 6 内联布局变量（--start-left/--start-top）
# 并用 MutationObserver 持续监听 .composer-main-row 的做法已废弃，原因：
#   1) clearInlineLayout() 会写 row.style（display/flexDirection/gap），而该元素本身
#      带有 composer-main-row 类且正是 Observer 的监听目标（attributeFilter:['style']），
#      于是每次修正都会重新触发自身回调，形成不会停止的同步自触发循环，
#      在移动端会持续占满主线程、耗电、甚至卡死页面。
#   2) 该脚本没有任何视口/媒体查询判断，会在所有设备上无条件把
#      composer-main-row 强制改成纵向堆叠布局，桌面端也会被误伤。
#   3) 手机端布局现由 unicalli_mobile.css / unicalli_mobile.js 独立维护；
#      unicalli_ui.css / unicalli_ui.js 只保留桌面视觉与跨端核心交互。
#      同一组件不再被多组移动端媒体查询和脚本重复接管。
_PREFIX_SCRIPT = (
    "<script>(function(){"
    "var p=location.pathname||'';"
    "if(p&&p!=='/'&&p.charAt(p.length-1)!=='/'){location.replace(location.href+'/');}"
    "var q=location.search||'';"
    "if(q.indexOf('diag')>-1){"
    "  function showDiag(){"
    "    var css=getComputedStyle(document.documentElement);"
    "    var bar=document.querySelector('.mobile-composer-bar');"
    "    var ta=document.querySelector('#text-input textarea');"
    "    var info={};"
    "    info.w=innerWidth; info.h=innerHeight;"
    "    info.mq767=matchMedia('(max-width: 767px)').matches;"
            "    info.hasPseudo=CSS.supports('selector(:has(a))');"
    "    info.colorMix=CSS.supports('color','color-mix(in srgb, red, blue)');"
    "    info.backdrop=CSS.supports('backdrop-filter','blur(1px)');"
    "    info.cssLoaded=!!(css.getPropertyValue('--bronze').trim());"
    "    info.bar=!!bar;"
    "    info.barBtns=bar?bar.querySelectorAll('button').length:-1;"
    "    info.uniCalli=!!window.UniCalli;"
    "    info.ready=document.readyState;"
    "    info.taBg=ta?getComputedStyle(ta).backgroundColor:'none';"
    "    info.taDisplay=ta?getComputedStyle(ta).display:'none';"
    "    info.bodyClass=document.body.className.slice(0,120);"
    "    info.ua=navigator.userAgent.slice(0,100);"
    "    var d=document.getElementById('unicalli-diag');"
    "    if(d)d.textContent=JSON.stringify(info,null,1);"
    "  }"
    "  function addDiag(){"
    "    var d=document.createElement('div');"
    "    d.id='unicalli-diag';"
    "    d.style.cssText='position:fixed;bottom:8px;left:8px;right:8px;z-index:99999;"
    "      background:rgba(0,0,0,.88);color:#fff;font:10px/1.4 monospace;padding:8px;"
    "      border-radius:6px;white-space:pre-wrap;pointer-events:none;word-break:break-all;';"
    "    document.body.appendChild(d);"
    "    setTimeout(showDiag,3500);"
    "    window.addEventListener('load',function(){setTimeout(showDiag,6000);});"
    "    window.addEventListener('resize',function(){setTimeout(showDiag,300);});"
    "  }"
    "  if(document.body){addDiag();}"
    "  else{document.addEventListener('DOMContentLoaded',addDiag);}"
    "}"
    "})();</script>"
)


class _InjectApiPrefixMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ctype = response.headers.get("content-type", "")
        if request.method == "GET" and response.status_code == 200 and "text/html" in ctype:
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            if "gradio_config" in text and "</head>" in text:
                text = text.replace("</head>", _PREFIX_SCRIPT + "</head>", 1)
                payload = text.encode("utf-8")
                headers = dict(response.headers)
                # 无缓存头时浏览器会对 HTML（含内联 CSS/JS）做启发式缓存，
                # 导致手机端加载旧版页面（移动端修复不生效）。强制 no-store。
                headers["cache-control"] = "no-store"
                headers["content-length"] = str(len(payload))
                return Response(content=payload, status_code=200, headers=headers, media_type="text/html")
        return response


demo.app.add_middleware(_InjectApiPrefixMiddleware)


# uvicorn 直启绕过 launch()，且 gradio 6.3.0 前端不会自动调用 /startup-events。
# 必须在 FastAPI 事件循环运行后再启动队列 worker：
# run_startup_events() 内部的 run_coro_in_background() 用 asyncio.get_event_loop()，
# 若在 uvicorn.run() 之前调用，worker 任务会挂在永不运行的事件循环上，
# 导致生成任务入队后无人消费、前端永远卡在"模型准备中"。
# 注意：不能使用 @app.on_event("startup")——gradio 自带的 lifespan
# （create_lifespan_handler）不调用 router.startup()，on_event 处理器永不触发。
# 正确做法是包装 demo.app 的 lifespan，在事件循环运行后启动 worker。
import contextlib

_old_lifespan = demo.app.router.lifespan_context


@contextlib.asynccontextmanager
async def _lifespan_with_queue_worker(app):
    async with _old_lifespan(app) as state:
        if not getattr(app, "startup_events_triggered", False):
            demo.run_startup_events()
            app.startup_events_triggered = True
        yield state


demo.app.router.lifespan_context = _lifespan_with_queue_worker


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        demo.app,
        host="0.0.0.0",
        port=55630,
        log_level="info",
    )
