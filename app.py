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
from unicalli_mobile_ui import render_mobile_stage_shell


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / ".unicalli_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

PROJECT = load_project_data(BASE_DIR)
AUTHOR_LIST = PROJECT.author_list
GENERATOR_SERVICE = GeneratorService(BASE_DIR)
DESKTOP_CSS = load_asset_text("unicalli_ui.css")
MOBILE_CSS = load_asset_text("unicalli_mobile.css")
CSS = ["unicalli_ui.css", "unicalli_mobile.css"]
INTERACTIONS_JS = load_asset_text("unicalli_ui.js")
MOBILE_INTERACTIONS_JS = load_asset_text("unicalli_mobile.js")
BOOTSTRAP_JS = f"{INTERACTIONS_JS}\n\n{MOBILE_INTERACTIONS_JS}"

GRADIO_MAJOR = int(gr.__version__.split(".", 1)[0])
BLOCKS_STYLE_KWARGS = {"css": CSS} if GRADIO_MAJOR < 6 else {}

INITIAL_AUTHOR = (
    "黄庭�?
    if "黄庭�? in PROJECT.author_fonts
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
            raise gr.Error("这幅长卷已过期，请另题一卷�?)
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
        return f"基础 Seed {session.base_seed}{suffix} · 重写�?{' / '.join(overrides)}"
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
        raise gr.Error("请先录入汉字�?)

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
        "起卷中�?,
        f"�?{len(segments)} �?,
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
                    raise RuntimeError("段落完成事件缺少图像�?)
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
            f"生成已停�?· {error}",
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
        raise gr.Error("无法识别要重写的段落�?) from error

    if target_index < 0 or target_index >= len(session.segments):
        raise gr.Error("目标段落超出范围�?)
    if session.images[target_index] is None:
        raise gr.Error("这一段尚未写成，暂不能重写�?)

    with SESSION_LOCK:
        if session.busy:
            raise gr.Error("当前仍有生成任务，请稍后再重写�?)
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
        f"�?{target_index + 1} �?· 准备重写",
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
                        f"�?{target_index + 1} �?· 显墨 "
                        f"{min((event.step or 0) + 1, event.total_steps or 1)}/"
                        f"{event.total_steps or 1}"
                    ),
                    _seed_summary(session),
                    gr.skip(),
                )

            elif event.type == "segment_completed":
                if event.image is None:
                    raise RuntimeError("重写完成事件缺少图像�?)
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
                    f"�?{target_index + 1} �?· 重写完成",
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
            f"�?{target_index + 1} �?· 重写未成，原图已保留 · {error}",
            _seed_summary(session),
            gr.skip(),
        )
        raise gr.Error(str(error)) from error


def generation_ui_stream_dual(
    text: str,
    author_dropdown: str,
    font_style: str,
    num_steps: int,
    seed: int,
    random_seed: bool,
    quant_mode: str,
    background_mode: str,
):
    """Mirror generation UI updates into desktop and mobile presentation trees."""
    for event, draft, status, seed_summary, download, session_id in generation_ui_stream(
        text,
        author_dropdown,
        font_style,
        num_steps,
        seed,
        random_seed,
        quant_mode,
        background_mode,
    ):
        yield (
            event,
            draft,
            status,
            seed_summary,
            download,
            session_id,
            draft,
            status,
            download,
        )


def reroll_segment_stream_dual(session_id: str, target_segment: Any):
    """Mirror reroll status/download updates into both presentation trees."""
    for event, draft, status, seed_summary, download in reroll_segment_stream(
        session_id, target_segment
    ):
        yield (
            event,
            draft,
            status,
            seed_summary,
            download,
            draft,
            status,
            download,
        )


def update_background_export(session_id: str, background_mode: str):
    """Keep browser theme and exported file in sync."""
    if not session_id:
        return gr.skip()

    session = _get_session(session_id)
    with SESSION_LOCK:
        session.background_mode = background_mode
    export_path = _export_session(session, session.base_seed)
    return _download_update(export_path) if export_path else gr.skip()


def update_background_export_pair(session_id: str, background_mode: str):
    update = update_background_export(session_id, background_mode)
    return update, update, background_mode


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
            <span class="topbar-seal" aria-hidden="true">�?/span>
            <span class="topbar-title">
              <strong>UniCalli</strong>
              <small>数字长卷</small>
            </span>
          </div>
          <div id="run-timer" aria-live="polite">
            <strong>00:00</strong><small>静�?/small>
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
                "回到当前�?, size="sm", elem_id="follow-current-btn"
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
                    "长卷待题�?, elem_id="status-line"
                )
                edit_again_btn = gr.Button(
                    "另题一�?, size="sm", elem_id="edit-again-btn"
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
                        "�?收藏",
                        size="sm",
                        elem_id="favorite-author-btn",
                    )
                    author_tag = gr.Textbox(
                        label="书家标注",
                        placeholder="常用、苍劲、待试…�?,
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
                        "全精�?,
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

    # ------------------------------------------------------------------
    # Phone UI: a separate component tree with its own information architecture.
    # It shares generation/session functions with desktop and never overlays it.
    # ------------------------------------------------------------------
    with gr.Column(elem_id="mobile-app"):
        with gr.Column(elem_id="mobile-compose-screen"):
            with gr.Row(elem_classes=["mobile-nav-row"]):
                gr.HTML(
                    """
                    <div class="mobile-brand">
                      <span class="mobile-brand-seal" aria-hidden="true">�?/span>
                      <span class="mobile-brand-copy">
                        <strong>UniCalli</strong><small>数字长卷</small>
                      </span>
                    </div>
                    """,
                    container=False,
                )
                mobile_open_settings = gr.Button(
                    "设置", size="sm", elem_id="mobile-open-settings"
                )

            with gr.Column(elem_id="mobile-compose-scroll"):
                gr.HTML(
                    """
                    <div class="mobile-compose-intro">
                      <em>掌中册页</em>
                      <h1>题写一�?/h1>
                      <p>只录汉字。每五字成一段，落笔后进入独立阅卷界面�?/p>
                    </div>
                    """,
                    container=False,
                )
                with gr.Column(elem_classes=["mobile-compose-form"]):
                    gr.HTML('<div class="mobile-field-caption">题写内容</div>', container=False)
                    mobile_text_input = gr.Textbox(
                        value=INITIAL_TEXT,
                        placeholder="山川异域风月同天",
                        label="题写内容",
                        show_label=False,
                        lines=4,
                        max_lines=7,
                        autofocus=False,
                        elem_id="mobile-text-input",
                    )
                    with gr.Row(elem_classes=["mobile-choice-row"]):
                        mobile_author_dropdown = gr.Dropdown(
                            choices=[SYNTHETIC_AUTHOR] + AUTHOR_LIST,
                            value=INITIAL_AUTHOR,
                            label="书家",
                            show_label=True,
                            elem_id="mobile-author-dropdown",
                        )
                        mobile_font_style = gr.Dropdown(
                            choices=INITIAL_FONT_CHOICES,
                            value=(INITIAL_FONT_CHOICES[0] if INITIAL_FONT_CHOICES else None),
                            label="书体",
                            show_label=True,
                            elem_id="mobile-font-dropdown",
                        )
                    mobile_draft_board = gr.HTML(
                        value=preview_text(INITIAL_TEXT),
                        elem_id="mobile-draft-board",
                        container=False,
                    )
                    mobile_generate_btn = gr.Button(
                        "落笔",
                        variant="primary",
                        elem_id="mobile-generate-btn",
                    )
                    gr.HTML(
                        '<div class="mobile-compose-footnote">生成参数与书家偏好收纳在设置页，不占用题写空间�?/div>',
                        container=False,
                    )

        with gr.Column(elem_id="mobile-settings-screen"):
            with gr.Row(elem_classes=["mobile-nav-row"]):
                mobile_settings_back = gr.Button(
                    "返回", size="sm", elem_id="mobile-settings-back"
                )
                gr.HTML(
                    """
                    <div class="mobile-brand">
                      <span class="mobile-brand-copy">
                        <strong>设置</strong><small>卷面 · 书家 · 生成</small>
                      </span>
                    </div>
                    """,
                    container=False,
                )
            with gr.Column(elem_id="mobile-settings-scroll"):
                with gr.Column(elem_classes=["mobile-settings-body"]):
                    gr.HTML(
                        """
                        <div class="mobile-settings-heading">
                          <strong>创作设置</strong>
                          <small>这里是独立页面。返回题写后，设置不会覆盖输入或作品�?/small>
                        </div>
                        """,
                        container=False,
                    )
                    with gr.Column(elem_classes=["mobile-settings-section"]):
                        gr.HTML('<div class="mobile-settings-section-title">卷面</div>', container=False)
                        mobile_theme_mode = gr.Radio(
                            choices=["纸白", "砚黑"],
                            value="纸白",
                            label="卷面",
                            show_label=False,
                            elem_id="mobile-theme-mode",
                        )

                    with gr.Column(elem_classes=["mobile-settings-section"]):
                        gr.HTML('<div class="mobile-settings-section-title">书家偏好</div>', container=False)
                        mobile_favorite_only = gr.Checkbox(
                            label="只看收藏",
                            value=False,
                            elem_id="mobile-favorite-only",
                        )
                        mobile_author_tag = gr.Textbox(
                            label="当前书家标注",
                            placeholder="常用、苍劲、待试…�?,
                            lines=1,
                            max_lines=1,
                            elem_id="mobile-author-tag",
                        )
                        with gr.Row(elem_classes=["mobile-settings-actions"]):
                            mobile_favorite_author_btn = gr.Button(
                                "�?收藏", size="sm", elem_id="mobile-favorite-author-btn"
                            )
                            mobile_save_author_tag_btn = gr.Button(
                                "保存标注", size="sm", elem_id="mobile-save-author-tag-btn"
                            )
                        mobile_preference_summary = gr.HTML(
                            value=render_author_preference_summary(DEFAULT_PREFERENCES),
                            elem_id="mobile-preference-summary",
                            container=False,
                        )

                    with gr.Column(elem_classes=["mobile-settings-section"]):
                        gr.HTML('<div class="mobile-settings-section-title">生成细节</div>', container=False)
                        mobile_num_steps = gr.Slider(
                            label="生成步数",
                            minimum=10,
                            maximum=100,
                            value=25,
                            step=1,
                            elem_id="mobile-num-steps",
                        )
                        mobile_seed = gr.Number(
                            label="种子", value=42, precision=0, elem_id="mobile-seed"
                        )
                        mobile_random_seed = gr.Checkbox(
                            label="每次随机", value=False, elem_id="mobile-random-seed"
                        )
                        mobile_quant_mode = gr.Radio(
                            label="计算精度",
                            choices=["8-bit（推荐）", "4-bit", "全精�?],
                            value="8-bit（推荐）",
                            elem_id="mobile-quant-mode",
                        )

        with gr.Column(elem_id="mobile-work-screen"):
            with gr.Row(elem_id="mobile-work-topbar"):
                mobile_edit_again_btn = gr.Button(
                    "另题", size="sm", elem_id="mobile-edit-again"
                )
                gr.HTML(
                    """
                    <div id="mobile-work-brand">
                      <span class="mobile-work-title">
                        <strong>UniCalli</strong><small>掌中阅卷</small>
                      </span>
                    </div>
                    """,
                    container=False,
                )
                mobile_download_btn = gr.DownloadButton(
                    "导出",
                    value=None,
                    visible=False,
                    size="sm",
                    elem_id="mobile-download-scroll",
                )

            mobile_stage_html = gr.HTML(
                value=render_mobile_stage_shell(),
                elem_id="mobile-stage-host",
                container=False,
                padding=False,
            )
            mobile_generation_status = gr.Markdown(
                "长卷待题�?, elem_id="mobile-generation-status"
            )

        mobile_reroll_target = gr.Textbox(
            value="", lines=1, elem_id="mobile-reroll-target"
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

    demo.load(
        fn=initialize_preferences,
        inputs=[preferences_state, mobile_author_dropdown],
        outputs=[
            mobile_author_dropdown,
            mobile_favorite_author_btn,
            mobile_author_tag,
            mobile_preference_summary,
        ],
        queue=False,
    )

    event_bus.change(
        fn=None,
        inputs=[event_bus],
        outputs=None,
        js="(event) => { window.UniCalli?.applyEvent(event); window.UniCalliMobile?.applyEvent(event); }",
        queue=False,
    )

    text_input.input(
        fn=preview_text,
        inputs=[text_input],
        outputs=[draft_board],
        queue=False,
    )

    mobile_text_input.input(
        fn=preview_text,
        inputs=[mobile_text_input],
        outputs=[mobile_draft_board],
        queue=False,
    )

    author_dropdown.change(
        fn=author_change,
        inputs=[author_dropdown, preferences_state],
        outputs=[font_style, favorite_author_btn, author_tag],
        queue=False,
    )

    mobile_author_dropdown.change(
        fn=author_change,
        inputs=[mobile_author_dropdown, preferences_state],
        outputs=[mobile_font_style, mobile_favorite_author_btn, mobile_author_tag],
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

    mobile_favorite_only.change(
        fn=filter_author_controls,
        inputs=[mobile_favorite_only, preferences_state, mobile_author_dropdown],
        outputs=[
            mobile_author_dropdown,
            mobile_font_style,
            mobile_favorite_author_btn,
            mobile_author_tag,
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

    mobile_favorite_author_btn.click(
        fn=toggle_author_favorite,
        inputs=[mobile_author_dropdown, preferences_state, mobile_favorite_only],
        outputs=[
            preferences_state,
            mobile_preference_summary,
            mobile_author_dropdown,
            mobile_font_style,
            mobile_favorite_author_btn,
            mobile_author_tag,
        ],
        queue=False,
    )

    save_author_tag_btn.click(
        fn=save_author_tag,
        inputs=[author_dropdown, author_tag, preferences_state],
        outputs=[preferences_state, preference_summary, author_tag],
        queue=False,
    )

    mobile_save_author_tag_btn.click(
        fn=save_author_tag,
        inputs=[mobile_author_dropdown, mobile_author_tag, preferences_state],
        outputs=[preferences_state, mobile_preference_summary, mobile_author_tag],
        queue=False,
    )

    theme_mode.change(
        fn=update_background_export_pair,
        inputs=[session_id_state, theme_mode],
        outputs=[download_btn, mobile_download_btn, mobile_theme_mode],
        js=(
            "(sessionId, mode) => { "
            "window.UniCalli?.setTheme(mode); "
            "window.UniCalliMobile?.setTheme(mode); "
            "return [sessionId, mode]; }"
        ),
        queue=False,
    )

    mobile_theme_mode.change(
        fn=update_background_export_pair,
        inputs=[session_id_state, mobile_theme_mode],
        outputs=[download_btn, mobile_download_btn, theme_mode],
        js=(
            "(sessionId, mode) => { "
            "window.UniCalli?.setTheme(mode); "
            "window.UniCalliMobile?.setTheme(mode); "
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

    mobile_open_settings.click(
        fn=None,
        js="() => { window.UniCalliMobile?.openSettings(); }",
        queue=False,
    )
    mobile_settings_back.click(
        fn=None,
        js="() => { window.UniCalliMobile?.closeSettings(); }",
        queue=False,
    )
    mobile_edit_again_btn.click(
        fn=None,
        js="() => { window.UniCalliMobile?.enterCompose(); }",
        queue=False,
    )

    generate_btn.click(
        fn=generation_ui_stream_dual,
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
            mobile_draft_board,
            mobile_generation_status,
            mobile_download_btn,
        ],
        show_progress="hidden",
        stream_every=0.12,
        js=(
            "(...args) => "
            "window.UniCalli ? window.UniCalli.beforeGenerate(args) : args"
        ),
    )

    mobile_generate_btn.click(
        fn=generation_ui_stream_dual,
        inputs=[
            mobile_text_input,
            mobile_author_dropdown,
            mobile_font_style,
            mobile_num_steps,
            mobile_seed,
            mobile_random_seed,
            mobile_quant_mode,
            mobile_theme_mode,
        ],
        outputs=[
            event_bus,
            draft_board,
            generation_status,
            seed_info,
            download_btn,
            session_id_state,
            mobile_draft_board,
            mobile_generation_status,
            mobile_download_btn,
        ],
        show_progress="hidden",
        stream_every=0.12,
        js=(
            "(...args) => "
            "window.UniCalliMobile ? window.UniCalliMobile.beforeGenerate(args) : args"
        ),
    )

    reroll_target.change(
        fn=reroll_segment_stream_dual,
        inputs=[session_id_state, reroll_target],
        outputs=[
            event_bus,
            draft_board,
            generation_status,
            seed_info,
            download_btn,
            mobile_draft_board,
            mobile_generation_status,
            mobile_download_btn,
        ],
        show_progress="hidden",
        stream_every=0.12,
    )

    mobile_reroll_target.change(
        fn=reroll_segment_stream_dual,
        inputs=[session_id_state, mobile_reroll_target],
        outputs=[
            event_bus,
            draft_board,
            generation_status,
            seed_info,
            download_btn,
            mobile_draft_board,
            mobile_generation_status,
            mobile_download_btn,
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
# URL 无尾斜杠�?gradio 相对路径 "./assets/" 会解析到根路径（打到 Funnel
# �?�?Plane）→ 注入脚本�?/unicalli 301 �?/unicalli/ 即可�?# 注意：不能改 api_prefix——gradio 后端已从请求正确推断 root（含 /unicalli），
# 前端 URL = root + api_prefix(/gradio_api) 已正确；注入 api_prefix 会导�?# root + 注入�?双前缀/畸形 URL（实�?404/405）�?# 说明：曾经通过 JS 在客户端清除 gradio 6 内联布局变量�?-start-left/--start-top�?# 并用 MutationObserver 持续监听 .composer-main-row 的做法已废弃，原因：
#   1) clearInlineLayout() 会写 row.style（display/flexDirection/gap），而该元素本身
#      带有 composer-main-row 类且正是 Observer 的监听目标（attributeFilter:['style']），
#      于是每次修正都会重新触发自身回调，形成不会停止的同步自触发循环，
#      在移动端会持续占满主线程、耗电、甚至卡死页面�?#   2) 该脚本没有任何视�?媒体查询判断，会在所有设备上无条件把
#      composer-main-row 强制改成纵向堆叠布局，桌面端也会被误伤�?#   3) 手机端使用独立的 Gradio 组件树与 unicalli_mobile.css/js�?#      桌面与手机共享生�?session/event bus，不再通过 fixed overlay �?!important
#      把桌面组件压缩成手机布局�?_PREFIX_SCRIPT = (
    "<script>(function(){"
    "var p=location.pathname||'';"
    "if(p&&p!=='/'&&p.charAt(p.length-1)!=='/'){location.replace(location.href+'/');}"
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
                headers["content-length"] = str(len(payload))
                return Response(content=payload, status_code=200, headers=headers, media_type="text/html")
        return response


demo.app.add_middleware(_InjectApiPrefixMiddleware)


# uvicorn 直启绕过 launch()，且 gradio 6.3.0 前端不会自动调用 /startup-events�?# 必须�?FastAPI 事件循环运行后再启动队列 worker�?# run_startup_events() 内部�?run_coro_in_background() �?asyncio.get_event_loop()�?# 若在 uvicorn.run() 之前调用，worker 任务会挂在永不运行的事件循环上，
# 导致生成任务入队后无人消费、前端永远卡�?模型准备�?�?# 注意：不能使�?@app.on_event("startup")——gradio 自带�?lifespan
# （create_lifespan_handler）不调用 router.startup()，on_event 处理器永不触发�?# 正确做法是包�?demo.app �?lifespan，在事件循环运行后启�?worker�?import contextlib

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


