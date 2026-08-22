# -*- coding: utf-8 -*-
"""Phone-specific UniCalli UI renderers.

The mobile DOM is intentionally separate from the desktop scroll workspace.
Both interfaces share the same Python generation/session functions and event bus.
"""
from __future__ import annotations


def render_mobile_stage_shell() -> str:
    return r"""
    <div class="mobile-work-canvas" aria-live="polite">
      <section class="mobile-generating-view" aria-label="正在生成书法">
        <div class="mobile-generation-copy">
          <span class="mobile-generation-kicker">墨迹生成中</span>
          <strong id="mobile-generation-title">静候首笔</strong>
          <small id="mobile-generation-meta">作品会逐段显墨</small>
        </div>
        <div class="mobile-live-art" data-has-image="false">
          <img id="mobile-live-image" alt="当前生成中的书法" draggable="false" />
          <div class="mobile-live-placeholder" aria-hidden="true">
            <i></i><span>候墨</span>
          </div>
        </div>
      </section>

      <section class="mobile-reading-view" aria-label="掌中阅卷">
        <div id="mobile-reading-track" class="mobile-reading-track"></div>
      </section>
    </div>
    """
