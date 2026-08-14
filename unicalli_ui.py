# -*- coding: utf-8 -*-
"""UniCalli UI renderers and browser preference helpers."""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from PIL import Image

from unicalli_core import PAD_CHARACTER, SegmentTask, SYNTHETIC_AUTHOR


def load_asset_text(filename: str) -> str:
    return (Path(__file__).resolve().parent / filename).read_text(encoding="utf-8")


def image_to_data_uri(image: Image.Image, quality: int = 82) -> str:
    """Encode one changing frame for the browser event bus.

    Completed images are cached on the PIL instance. Preview frames are transient.
    """
    cache_key = f"_unicalli_data_uri_{quality}"
    cached = image.info.get(cache_key)
    if isinstance(cached, str):
        return cached

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="WEBP", quality=quality, method=4)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    uri = f"data:image/webp;base64,{encoded}"
    image.info[cache_key] = uri
    return uri


def render_stage_shell() -> str:
    """Render one permanent stage. JavaScript patches individual segment nodes."""
    return """
    <div class="scroll-stage-shell" data-stage-state="empty"
         tabindex="0" role="region" aria-label="书法横向长卷">
      <div class="stage-empty-copy">
        <span class="stage-empty-index">数字书法长卷</span>
        <strong>卷上无墨</strong>
        <p>录入汉字，落笔后长卷自右向左展开。</p>
      </div>
      <div id="scroll-track" class="scroll-track is-empty" aria-live="polite"></div>
    </div>
    """


def render_empty_stage() -> str:
    """Backward-compatible alias."""
    return render_stage_shell()


def segment_payloads(segments: Sequence[SegmentTask]) -> List[Dict[str, Any]]:
    return [
        {
            "index": int(segment.index),
            "display_text": segment.display_text,
            "is_padded": bool(segment.is_padded),
        }
        for segment in segments
    ]


def render_draft_strip(
    segments: Sequence[SegmentTask],
    active_index: Optional[int] = None,
    completed_indices: Optional[Iterable[int]] = None,
) -> str:
    if not segments:
        return (
            '<div class="draft-strip is-empty">'
            '<span>只收汉字；每五字一段，末段以「□」补足。</span>'
            "</div>"
        )

    completed = set(completed_indices or [])
    items: List[str] = []
    for segment in segments:
        state_class = ""
        state_text = "待书"
        if segment.index == active_index:
            state_class = "is-active"
            state_text = "书写中"
        elif segment.index in completed:
            state_class = "is-done"
            state_text = "已成"

        items.append(
            f'<span class="draft-token {state_class}" data-draft-index="{segment.index}">'
            f'<b>{html.escape(segment.display_text)}</b>'
            f'<small>{state_text}</small></span>'
        )

    padded = sum(segment.display_text.count(PAD_CHARACTER) for segment in segments)
    meta = (
        f"共 {len(segments)} 段 · 补位 {padded} 字"
        if padded
        else f"共 {len(segments)} 段"
    )
    return (
        '<div class="draft-strip">'
        f'<div class="draft-meta"><span>文字分段</span><span>{meta}</span></div>'
        f'<div class="draft-tokens">{"".join(items)}</div>'
        "</div>"
    )


def normalize_preferences(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"favorites": [], "tags": {}}

    favorites = value.get("favorites")
    tags = value.get("tags")
    normalized_favorites: List[str] = []
    if isinstance(favorites, list):
        seen = set()
        for item in favorites:
            author = str(item)
            if author not in seen:
                normalized_favorites.append(author)
                seen.add(author)

    return {
        "favorites": normalized_favorites,
        "tags": (
            {str(key): str(tag) for key, tag in tags.items()}
            if isinstance(tags, dict)
            else {}
        ),
    }


def favorite_button_label(author: str, preferences: Any) -> str:
    prefs = normalize_preferences(preferences)
    return "★ 已收藏" if author in prefs["favorites"] else "☆ 收藏"


def author_tag_value(author: str, preferences: Any) -> str:
    prefs = normalize_preferences(preferences)
    return prefs["tags"].get(author, "")


def render_author_preference_summary(preferences: Any) -> str:
    prefs = normalize_preferences(preferences)
    favorites = prefs["favorites"]
    if not favorites:
        return (
            '<div class="preference-summary is-empty">'
            "<span>尚无收藏</span></div>"
        )

    items: List[str] = []
    for author in favorites:
        tag = prefs["tags"].get(author, "")
        tag_markup = f"<small>{html.escape(tag)}</small>" if tag else ""
        items.append(f"<span><b>{html.escape(author)}</b>{tag_markup}</span>")

    return (
        '<div class="preference-summary">'
        '<em>已收藏</em>'
        + "".join(items)
        + "</div>"
    )


def ordered_author_choices(
    all_authors: Sequence[str],
    preferences: Any,
    favorites_only: bool,
) -> List[str]:
    prefs = normalize_preferences(preferences)
    favorite_set = set(prefs["favorites"])
    favorites = [author for author in prefs["favorites"] if author in all_authors]
    others = [author for author in all_authors if author not in favorite_set]

    if favorites_only:
        return [SYNTHETIC_AUTHOR] + favorites
    return [SYNTHETIC_AUTHOR] + favorites + others
