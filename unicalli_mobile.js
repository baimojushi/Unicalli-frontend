(() => {
  if (window.UniCalliMobile) return;

  const MOBILE_QUERY = "(max-width: 767px)";
  const media = window.matchMedia(MOBILE_QUERY);

  const state = {
    composerOpen: true,
    settingsOpen: false,
    dockObserver: null,
    drawerObserver: null,
    domObserver: null,
    bodyObserver: null,
    lastWriting: false,
    lastComplete: false,
  };

  const byId = (id) => document.getElementById(id);
  const body = () => document.body;
  const root = () => document.documentElement;
  const active = () => media.matches;

  function syncStageCopy() {
    const stage = document.querySelector(
      "#scroll-stage-host .scroll-stage-shell"
    );
    if (!stage) return;

    const empty = stage.querySelector(".stage-empty-copy");
    if (!empty) return;

    const index = empty.querySelector(".stage-empty-index");
    const title = empty.querySelector("strong");
    const copy = empty.querySelector("p");

    if (active()) {
      stage.setAttribute("aria-label", "书法长卷，纵向阅卷");
      if (index) index.textContent = "掌中长卷 · 纵向阅卷";

      if (stage.dataset.stageState === "empty") {
        if (title) title.textContent = "静候落笔";
        if (copy) {
          copy.textContent =
            "题写汉字后，墨迹逐段铺展。上下滑动阅卷，每段均可独立重写。";
        }
      }
    } else {
      stage.setAttribute("aria-label", "书法长卷，横向展卷");
      if (index) index.textContent = "横卷 · 右起左展";

      if (stage.dataset.stageState === "empty") {
        if (title) title.textContent = "静候落笔";
        if (copy) {
          copy.textContent =
            "输入汉字后落笔，墨迹将从右向左连续铺展。";
        }
      }
    }
  }

  function ensureBar() {
    const dock = byId("composer-dock");
    if (!dock) return false;

    if (!dock.querySelector(".mobile-composer-bar")) {
      const bar = document.createElement("div");
      bar.className = "mobile-composer-bar";
      bar.innerHTML = `
        <span class="mobile-composer-title">
          <strong>题写长卷</strong>
          <small>内容 · 书家 · 书体</small>
        </span>
        <button
          class="mobile-settings-toggle"
          type="button"
          aria-expanded="false"
          aria-controls="side-drawers"
        >设置</button>
        <button
          class="mobile-composer-toggle"
          type="button"
          aria-expanded="true"
          aria-controls="text-input"
          aria-label="展开或收起题写面板"
        ></button>`;
      dock.prepend(bar);
    }

    return true;
  }

  function setComposerOpen(open) {
    state.composerOpen = Boolean(open);
    if (!active()) return;

    body().classList.toggle(
      "unicalli-mobile-composer-collapsed",
      !state.composerOpen
    );

    const toggle = document.querySelector(".mobile-composer-toggle");
    if (toggle) {
      toggle.setAttribute(
        "aria-expanded",
        String(state.composerOpen)
      );
    }

    requestAnimationFrame(updateMetrics);
  }

  function setSettingsOpen(open) {
    state.settingsOpen = Boolean(open);
    if (!active()) return;

    body().classList.toggle(
      "unicalli-mobile-settings-open",
      state.settingsOpen
    );

    const toggle = document.querySelector(".mobile-settings-toggle");
    if (toggle) {
      toggle.setAttribute(
        "aria-expanded",
        String(state.settingsOpen)
      );
    }

    requestAnimationFrame(updateMetrics);
  }

  function closeSettings() {
    setSettingsOpen(false);
  }

  function closeSheets() {
    setSettingsOpen(false);
    setComposerOpen(false);
  }

  function visualViewportBottom() {
    const viewport = window.visualViewport;
    if (!viewport) return 0;

    return Math.max(
      0,
      Math.round(
        window.innerHeight -
        viewport.height -
        viewport.offsetTop
      )
    );
  }

  function updateMetrics() {
    if (!active()) {
      root().style.removeProperty("--mobile-dock-height");
      root().style.removeProperty("--mobile-settings-height");
      root().style.removeProperty("--mobile-vv-bottom");
      root().style.removeProperty("--mobile-reserved-bottom");
      return;
    }

    const dock = byId("composer-dock");
    const drawer = byId("side-drawers");

    const dockHeight = dock
      ? Math.ceil(dock.getBoundingClientRect().height)
      : 0;

    const drawerHeight =
      state.settingsOpen && drawer
        ? Math.ceil(drawer.getBoundingClientRect().height) + 8
        : 0;

    const vvBottom = visualViewportBottom();
    const reserved = Math.max(
      72,
      dockHeight + drawerHeight + vvBottom + 18
    );

    root().style.setProperty(
      "--mobile-dock-height",
      `${dockHeight}px`
    );
    root().style.setProperty(
      "--mobile-settings-height",
      `${drawerHeight}px`
    );
    root().style.setProperty(
      "--mobile-vv-bottom",
      `${vvBottom}px`
    );
    root().style.setProperty(
      "--mobile-reserved-bottom",
      `${reserved}px`
    );
  }

  function bindObservers() {
    const dock = byId("composer-dock");
    const drawer = byId("side-drawers");

    if (window.ResizeObserver && dock && !state.dockObserver) {
      state.dockObserver = new ResizeObserver(updateMetrics);
      state.dockObserver.observe(dock);
    }

    if (window.ResizeObserver && drawer && !state.drawerObserver) {
      state.drawerObserver = new ResizeObserver(updateMetrics);
      state.drawerObserver.observe(drawer);
    }

    if (!state.bodyObserver && body()) {
      state.bodyObserver = new MutationObserver(() => {
        if (!active()) return;

        const writing =
          body().classList.contains("unicalli-writing");
        const complete =
          body().classList.contains("unicalli-complete");

        if (
          (writing && !state.lastWriting) ||
          (complete && !state.lastComplete)
        ) {
          closeSheets();
        }

        state.lastWriting = writing;
        state.lastComplete = complete;
        requestAnimationFrame(updateMetrics);
      });

      state.bodyObserver.observe(body(), {
        attributes: true,
        attributeFilter: ["class"],
      });
    }

    if (!state.domObserver) {
      state.domObserver = new MutationObserver(() => {
        if (!active()) return;

        if (ensureBar()) {
          bindObservers();
          syncStageCopy();
          requestAnimationFrame(updateMetrics);
        }
      });

      state.domObserver.observe(document.documentElement, {
        childList: true,
        subtree: true,
      });
    }
  }

  function activate() {
    if (!body()) return;

    if (!active()) {
      body().classList.remove(
        "unicalli-mobile-ui",
        "unicalli-mobile-composer-collapsed",
        "unicalli-mobile-settings-open"
      );
      updateMetrics();
      syncStageCopy();
      return;
    }

    body().classList.add("unicalli-mobile-ui");
    ensureBar();
    bindObservers();
    setComposerOpen(state.composerOpen);
    setSettingsOpen(state.settingsOpen);
    syncStageCopy();
    requestAnimationFrame(updateMetrics);
  }

  document.addEventListener(
    "click",
    (event) => {
      if (!active()) return;

      const composerToggle = event.target.closest(
        ".mobile-composer-toggle"
      );

      if (composerToggle) {
        event.preventDefault();
        setSettingsOpen(false);
        setComposerOpen(!state.composerOpen);
        return;
      }

      const settingsToggle = event.target.closest(
        ".mobile-settings-toggle"
      );

      if (settingsToggle) {
        event.preventDefault();
        const next = !state.settingsOpen;
        setComposerOpen(false);
        setSettingsOpen(next);
        return;
      }

      if (event.target.closest("#edit-again-btn")) {
        setSettingsOpen(false);
        setComposerOpen(true);
      }
    },
    true
  );

  document.addEventListener("keydown", (event) => {
    if (!active() || event.key !== "Escape") return;

    if (state.settingsOpen) {
      setSettingsOpen(false);
    } else if (state.composerOpen) {
      setComposerOpen(false);
    }
  });

  if (media.addEventListener) {
    media.addEventListener("change", activate);
  } else if (media.addListener) {
    media.addListener(activate);
  }

  window.addEventListener("resize", activate, {
    passive: true,
  });

  window.addEventListener(
    "orientationchange",
    () => window.setTimeout(activate, 80),
    { passive: true }
  );

  window.visualViewport?.addEventListener(
    "resize",
    updateMetrics,
    { passive: true }
  );

  window.visualViewport?.addEventListener(
    "scroll",
    updateMetrics,
    { passive: true }
  );

  window.UniCalliMobile = {
    activate,
    closeSettings,
    closeSheets,
    setComposerOpen,
    setSettingsOpen,
    updateMetrics,
  };

  activate();
  window.setTimeout(activate, 120);
  window.setTimeout(activate, 600);
})();
