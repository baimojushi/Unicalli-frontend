(() => {
  if (window.UniCalliV4) return;

  const state = {
    autoFollow: true,
    programmaticScroll: false,
    stage: null,
    track: null,
    segments: new Map(),
    activeIndex: null,
    lastRerollIndex: null,
    rerollPendingIndex: null,
    rerollBridgeTimer: null,
    settleTimer: null,
    runTimer: null,
    runStartedAt: null,
    runMode: "静候",
    depthFrame: null,
    idleTimer: null,
    inputBound: false,
    mobileControlsBound: false,
    mobileComposerOpen: true,
    mobileSettingsOpen: false,
  };

  const byId = (id) => document.getElementById(id);
  const body = () => document.body;
  const root = () => document.documentElement;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  const MOBILE_QUERY = "(max-width: 640px)";

  function isMobileLayout() {
    return window.matchMedia(MOBILE_QUERY).matches;
  }

  function syncMobileStageCopy() {
    const stage = state.stage || findStage();
    if (!stage) return;
    const emptyCopy = stage.querySelector(".stage-empty-copy");
    if (!emptyCopy) return;
    const index = emptyCopy.querySelector(".stage-empty-index");
    const title = emptyCopy.querySelector("strong");
    const copy = emptyCopy.querySelector("p");
    if (isMobileLayout()) {
      if (index) index.textContent = "掌中长卷 · 纵向阅卷";
      if (title && stage.dataset.stageState === "empty") title.textContent = "静候落笔";
      if (copy && stage.dataset.stageState === "empty") copy.textContent = "题写汉字后，墨迹将逐段铺展。上下滑动即可阅卷。";
    } else {
      if (index) index.textContent = "横卷 · 右起左展";
      if (title && stage.dataset.stageState === "empty") title.textContent = "静候落笔";
      if (copy && stage.dataset.stageState === "empty") copy.textContent = "输入汉字后落笔，墨迹将从右向左连续铺展。";
    }
  }

  // 展开/收起改为在 body 上维护 "collapsed" 类而不是 "open" 类：
  // CSS 中题写区默认可见（见 unicalli_ui.css 对应 @media 块），这里只在
  // 用户主动收起、或写作/完成态时补一个 collapsed 类。即便本函数因为任何
  // 原因未能及时执行（组件尚未挂载、matchMedia 时序问题等），题写区仍然
  // 保持默认可见，不会出现"看不到输入框"的情况。
  function setMobileComposerOpen(open) {
    state.mobileComposerOpen = Boolean(open);
    if (!isMobileLayout()) {
      body().classList.remove("unicalli-mobile-composer-collapsed");
      return;
    }
    body().classList.toggle("unicalli-mobile-composer-collapsed", !state.mobileComposerOpen);
    const button = document.querySelector(".mobile-composer-toggle");
    if (button) button.setAttribute("aria-expanded", String(state.mobileComposerOpen));
  }

  function setMobileSettingsOpen(open) {
    state.mobileSettingsOpen = Boolean(open);
    if (!isMobileLayout()) {
      body().classList.remove("unicalli-mobile-settings-open");
      return;
    }
    body().classList.toggle("unicalli-mobile-settings-open", state.mobileSettingsOpen);
    const button = document.querySelector(".mobile-settings-toggle");
    if (button) button.setAttribute("aria-expanded", String(state.mobileSettingsOpen));
  }

  function ensureMobileControls() {
    const dock = byId("composer-dock");
    if (!dock) {
      window.setTimeout(ensureMobileControls, 100);
      return;
    }

    if (!dock.querySelector(".mobile-composer-bar")) {
      const bar = document.createElement("div");
      bar.className = "mobile-composer-bar";
      bar.innerHTML = `
        <span class="mobile-composer-title">
          <strong>题写长卷</strong>
          <small>输入、书家与书体</small>
        </span>
        <button class="mobile-settings-toggle" type="button" aria-expanded="false" aria-controls="side-drawers">设置</button>
        <button class="mobile-composer-toggle" type="button" aria-expanded="true" aria-controls="text-input" aria-label="展开或收起题写面板"></button>`;
      dock.prepend(bar);
      // 点击行为统一由上面 document 级别的委托监听器处理，这里不再单独绑定，
      // 避免这个节点被 Gradio 重渲染替换后监听器悬空失效。
    }

    if (!document.querySelector(".mobile-sheet-backdrop")) {
      const backdrop = document.createElement("button");
      backdrop.type = "button";
      backdrop.className = "mobile-sheet-backdrop";
      backdrop.setAttribute("aria-label", "关闭设置面板");
      backdrop.addEventListener("click", () => setMobileSettingsOpen(false));
      document.body.append(backdrop);
    }

    state.mobileControlsBound = true;
    syncResponsiveUi();
  }

  function syncResponsiveUi() {
    if (!isMobileLayout()) {
      body().classList.remove(
        "unicalli-mobile-composer-collapsed",
        "unicalli-mobile-settings-open"
      );
      syncMobileStageCopy();
      requestCaptionDepth();
      return;
    }

    // Gradio 的 Svelte 重渲染可能把 composer-dock 的子节点整体替换掉，连带
    // 抹掉我们注入的 .mobile-composer-bar。syncResponsiveUi 本身会在 resize、
    // 流式生成事件等多个时机被调用，顺带检查一下，缺失就补种，成本很低。
    const dock = byId("composer-dock");
    if (dock && !dock.querySelector(".mobile-composer-bar")) {
      ensureMobileControls();
    }

    if (body().classList.contains("unicalli-writing") || body().classList.contains("unicalli-complete")) {
      state.mobileComposerOpen = false;
      state.mobileSettingsOpen = false;
    }
    setMobileComposerOpen(state.mobileComposerOpen);
    setMobileSettingsOpen(state.mobileSettingsOpen);
    syncMobileStageCopy();
    requestCaptionDepth();
  }

  function findStage() {
    return document.querySelector("#scroll-stage-host .scroll-stage-shell");
  }

  function findTrack() {
    return document.querySelector("#scroll-stage-host #scroll-track");
  }

  function segmentNode(index) {
    return state.track?.querySelector(
      `.scroll-segment[data-segment-index="${Number(index)}"]`
    ) || null;
  }

  function setManualMode(manual) {
    state.autoFollow = !manual;
    body().classList.toggle("unicalli-manual-scroll", manual);
  }

  function formatElapsed(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  }

  function paintTimer() {
    const host = byId("run-timer");
    if (!host) return;
    const value = host.querySelector("strong");
    const label = host.querySelector("small");
    if (value) {
      value.textContent = state.runStartedAt
        ? formatElapsed(Date.now() - state.runStartedAt)
        : value.textContent || "00:00";
    }
    if (label) label.textContent = state.runMode;
  }

  function startTimer(mode) {
    stopTimer(mode, false);
    state.runMode = mode;
    state.runStartedAt = Date.now();
    state.runTimer = window.setInterval(paintTimer, 250);
    paintTimer();
    body().classList.add("unicalli-timing");
    scheduleUiRest();
  }

  function stopTimer(mode = "已成", preserveElapsed = true) {
    if (state.runTimer) {
      window.clearInterval(state.runTimer);
      state.runTimer = null;
    }
    if (!preserveElapsed) {
      state.runStartedAt = null;
      const value = byId("run-timer")?.querySelector("strong");
      if (value) value.textContent = "00:00";
    } else if (state.runStartedAt) {
      paintTimer();
      state.runStartedAt = null;
    }
    state.runMode = mode;
    paintTimer();
    body().classList.remove("unicalli-timing");
    scheduleUiRest();
  }

  function setRatio(article, image) {
    if (!article || !image?.naturalWidth || !image?.naturalHeight) return;
    article.style.setProperty(
      "--segment-ratio",
      String(image.naturalWidth / image.naturalHeight)
    );
    requestCaptionDepth();
  }

  function makeSegment(segment) {
    const article = document.createElement("article");
    article.className = "scroll-segment is-pending";
    article.dataset.segmentIndex = String(segment.index);
    article.style.setProperty("--decode-progress", "0.04");
    article.style.setProperty("--segment-ratio", "1");
    article.style.setProperty("--caption-scale", "1");
    article.style.setProperty("--caption-opacity", "1");
    article.style.setProperty("--caption-shift", "0px");
    article.style.setProperty("--caption-font-size", "15px");

    const captionWrap = document.createElement("div");
    captionWrap.className = "segment-caption-wrap";

    const caption = document.createElement("div");
    caption.className = "segment-caption";

    const meta = document.createElement("span");
    meta.className = "caption-meta";

    const index = document.createElement("span");
    index.className = "caption-index";
    index.textContent = String(segment.index + 1).padStart(2, "0");

    const status = document.createElement("span");
    status.className = "caption-state";
    status.textContent = "候写";

    const text = document.createElement("span");
    text.className = "caption-text";
    text.textContent = segment.display_text;

    const reroll = document.createElement("button");
    reroll.className = "segment-reroll";
    reroll.type = "button";
    reroll.dataset.rerollIndex = String(segment.index);
    reroll.textContent = "重写";
    reroll.disabled = true;
    reroll.setAttribute("aria-label", `重写第 ${segment.index + 1} 段`);

    meta.append(index, status);
    caption.append(meta, text, reroll);
    captionWrap.append(caption);

    const imageWrap = document.createElement("div");
    imageWrap.className = "segment-image-wrap";

    const finalImage = document.createElement("img");
    finalImage.className = "segment-final-image";
    finalImage.alt = `第 ${segment.index + 1} 段书法图`;
    finalImage.draggable = false;
    finalImage.addEventListener("load", () => setRatio(article, finalImage));

    const previewImage = document.createElement("img");
    previewImage.className = "segment-preview-image";
    previewImage.alt = "";
    previewImage.draggable = false;
    previewImage.addEventListener("load", () => setRatio(article, previewImage));

    const placeholder = document.createElement("div");
    placeholder.className = "segment-placeholder";
    placeholder.innerHTML = '<span class="ink-breath"></span><small>候墨</small>';

    const rerollMask = document.createElement("div");
    rerollMask.className = "segment-reroll-mask";
    rerollMask.innerHTML = '<span class="ink-breath"></span><small>重写中</small>';

    imageWrap.append(finalImage, previewImage, placeholder, rerollMask);
    article.append(captionWrap, imageWrap);
    return article;
  }

  function insertSegmentInOrder(article, index) {
    const children = Array.from(state.track?.children || []);
    const before = children.find(
      (node) => Number(node.dataset.segmentIndex) < Number(index)
    );
    if (before) state.track.insertBefore(article, before);
    else state.track.append(article);
  }

  function ensureSegment(index) {
    let article = segmentNode(index);
    if (article) return article;

    const segment = state.segments.get(Number(index));
    if (!segment || !state.track || !state.stage) return null;

    article = makeSegment(segment);
    insertSegmentInOrder(article, index);
    state.track.classList.remove("is-empty");
    state.stage.dataset.stageState = "active";
    const emptyCopy = state.stage.querySelector(".stage-empty-copy");
    if (emptyCopy) emptyCopy.hidden = true;
    requestCaptionDepth();
    return article;
  }

  function resetStage(segments) {
    if (!state.stage || !state.track) bindStage();
    if (!state.stage || !state.track) return;

    clearRerollBridge();
    state.segments = new Map(
      (segments || []).map((segment) => [Number(segment.index), segment])
    );
    state.track.replaceChildren();
    state.track.classList.add("is-empty");
    state.stage.dataset.stageState = "preparing";

    const emptyCopy = state.stage.querySelector(".stage-empty-copy");
    if (emptyCopy) {
      emptyCopy.hidden = false;
      const title = emptyCopy.querySelector("strong");
      const copy = emptyCopy.querySelector("p");
      if (title) title.textContent = "卷面已备";
      if (copy) {
        copy.textContent = isMobileLayout()
          ? "正在候墨，首段完成后会出现在当前视野。"
          : "正在候墨，首段将从右侧显现。";
      }
    }

    state.activeIndex = null;
    state.lastRerollIndex = null;
    state.rerollPendingIndex = null;
    setManualMode(false);
    state.stage.scrollLeft = state.stage.scrollWidth;
  }

  const SEGMENT_STATE_CLASSES = [
    "is-pending",
    "is-entering",
    "is-streaming",
    "is-complete",
    "is-rerolling",
    "is-reroll-requested",
    "has-error",
  ];

  function setSegmentState(index, nextClass, label) {
    const article = ensureSegment(index);
    if (!article) return null;
    SEGMENT_STATE_CLASSES.forEach((name) => article.classList.remove(name));
    if (nextClass) article.classList.add(nextClass);
    const status = article.querySelector(".caption-state");
    if (status && label) status.textContent = label;
    return article;
  }

  function beginImageReveal(article) {
    if (!article) return;
    article.classList.remove("is-image-revealing");
    void article.offsetWidth;
    article.classList.add("is-image-revealing");
    window.setTimeout(() => {
      article.classList.remove("is-image-revealing");
    }, 1600);
  }

  function setPreview(index, src, step, totalSteps, reroll = false) {
    const current = Math.min(Number(step) + 1, Number(totalSteps) || 1);
    const article = setSegmentState(
      index,
      reroll ? "is-rerolling" : "is-streaming",
      `显墨 ${current}/${Number(totalSteps) || 1}`
    );
    if (!article) return;

    const progress = clamp(
      (Number(step) + 1) / (Number(totalSteps) || 1),
      0.04,
      1
    );
    article.style.setProperty("--decode-progress", String(progress));
    if (reroll) article.classList.add("has-preview");

    const preview = article.querySelector(".segment-preview-image");
    if (preview && preview.src !== src) {
      const firstFrame = article.dataset.previewSeen !== "true";
      preview.src = src;
      if (firstFrame) {
        article.dataset.previewSeen = "true";
        beginImageReveal(article);
      }
    }
  }

  function completeSegment(index, src, seed) {
    const article = setSegmentState(index, "is-complete", "定墨");
    if (!article) return;

    article.style.setProperty("--decode-progress", "1");
    article.classList.remove("has-preview", "has-error");
    if (seed !== undefined && seed !== null) article.dataset.seed = String(seed);

    const finalImage = article.querySelector(".segment-final-image");
    if (finalImage) {
      const reveal = () => beginImageReveal(article);
      finalImage.addEventListener("load", reveal, { once: true });
      if (finalImage.src !== src) finalImage.src = src;
      else reveal();
    }

    const preview = article.querySelector(".segment-preview-image");
    if (preview) preview.removeAttribute("src");

    const reroll = article.querySelector(".segment-reroll");
    if (reroll) reroll.disabled = false;
    article.dataset.previewSeen = "false";
    requestCaptionDepth();
  }

  function failReroll(index) {
    const article = segmentNode(index);
    if (!article) return;
    setSegmentState(index, "is-complete", "原图保留");
    article.classList.add("has-error");
    const preview = article.querySelector(".segment-preview-image");
    if (preview) preview.removeAttribute("src");
    const reroll = article.querySelector(".segment-reroll");
    if (reroll) reroll.disabled = false;
    article.dataset.previewSeen = "false";
  }

  function followIndex(index, smooth = true) {
    const target = segmentNode(index);
    if (!target) return;

    state.programmaticScroll = true;
    target.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
      block: isMobileLayout() ? "center" : "nearest",
      inline: isMobileLayout() ? "nearest" : "center",
    });
    window.clearTimeout(state.settleTimer);
    state.settleTimer = window.setTimeout(() => {
      state.programmaticScroll = false;
      requestCaptionDepth();
    }, smooth ? 720 : 80);
  }

  function followCurrent(smooth = true) {
    const fallback = Number(
      state.track?.querySelector(".scroll-segment")?.dataset.segmentIndex
    );
    const index = state.activeIndex ?? state.lastRerollIndex ?? fallback;
    if (Number.isFinite(index)) followIndex(index, smooth);
  }

  function updateCaptionDepth() {
    state.depthFrame = null;
    if (!state.stage || !state.track) return;

    const stageRect = state.stage.getBoundingClientRect();
    const vertical = isMobileLayout();
    const half = Math.max(1, vertical ? stageRect.height / 2 : stageRect.width / 2);
    const center = vertical
      ? stageRect.top + half
      : stageRect.left + half;

    state.track.querySelectorAll(".scroll-segment").forEach((article) => {
      const rect = article.getBoundingClientRect();
      const itemCenter = vertical
        ? rect.top + rect.height / 2
        : rect.left + rect.width / 2;
      const distance = Math.abs(itemCenter - center) / half;
      const depth = clamp((distance - (vertical ? 0.32 : 0.43)) / (vertical ? 0.68 : 0.57), 0, 1);
      article.style.setProperty("--caption-scale", String(1 - depth * (vertical ? 0.03 : 0.065)));
      article.style.setProperty("--caption-opacity", String(1 - depth * (vertical ? 0.18 : 0.24)));
      article.style.setProperty("--caption-shift", `${depth * (vertical ? 2 : 5)}px`);
      article.style.setProperty("--caption-font-size", `${(vertical ? 13 : 15) - depth * (vertical ? 1 : 2)}px`);
    });
  }

  function requestCaptionDepth() {
    if (state.depthFrame) return;
    state.depthFrame = requestAnimationFrame(updateCaptionDepth);
  }

  function clearRerollBridge() {
    window.clearTimeout(state.rerollBridgeTimer);
    state.rerollBridgeTimer = null;
    state.rerollPendingIndex = null;
  }

  function applyEvent(event) {
    if (!event || typeof event !== "object") return;
    const kind = event.kind;
    const index = Number(event.index);

    if (kind === "reset") {
      resetStage(event.segments || []);
      startTimer("生成中");
      body().classList.remove("unicalli-complete");
      body().classList.add("unicalli-writing");
      return;
    }

    if (kind === "task_started") {
      startTimer("生成中");
      return;
    }

    if (kind === "segment_started") {
      state.activeIndex = index;
      const article = setSegmentState(index, "is-entering", "候墨");
      if (article) {
        const reroll = article.querySelector(".segment-reroll");
        if (reroll) reroll.disabled = true;
      }
      if (state.autoFollow) requestAnimationFrame(() => followIndex(index, true));
      return;
    }

    if (kind === "preview") {
      state.activeIndex = index;
      setPreview(index, event.image, event.step, event.total_steps, false);
      return;
    }

    if (kind === "segment_completed") {
      state.activeIndex = index;
      completeSegment(index, event.image, event.seed);
      return;
    }

    if (kind === "task_completed") {
      stopTimer("已成");
      body().classList.remove("unicalli-writing");
      body().classList.add("unicalli-complete");
      scheduleUiRest();
      return;
    }

    if (kind === "task_error") {
      stopTimer("已停");
      body().classList.remove("unicalli-writing");
      body().classList.add("unicalli-complete");
      scheduleUiRest();
      return;
    }

    if (kind === "reroll_started") {
      clearRerollBridge();
      state.activeIndex = index;
      state.lastRerollIndex = index;
      startTimer("重写中");
      body().classList.remove("unicalli-complete");
      body().classList.add("unicalli-writing");

      const article = setSegmentState(index, "is-rerolling", "重写中");
      const reroll = article?.querySelector(".segment-reroll");
      if (reroll) reroll.disabled = true;
      requestAnimationFrame(() => followIndex(index, true));
      return;
    }

    if (kind === "reroll_preview") {
      state.activeIndex = index;
      setPreview(index, event.image, event.step, event.total_steps, true);
      return;
    }

    if (kind === "reroll_completed") {
      clearRerollBridge();
      state.activeIndex = index;
      state.lastRerollIndex = index;
      completeSegment(index, event.image, event.seed);
      stopTimer("重写完成");
      body().classList.remove("unicalli-writing");
      body().classList.add("unicalli-complete");
      requestAnimationFrame(() => followIndex(index, true));
      return;
    }

    if (kind === "reroll_error") {
      clearRerollBridge();
      state.activeIndex = index;
      state.lastRerollIndex = index;
      failReroll(index);
      stopTimer("重写未成");
      body().classList.remove("unicalli-writing");
      body().classList.add("unicalli-complete");
      requestAnimationFrame(() => followIndex(index, true));
    }
  }

  function bindStage() {
    const stage = findStage();
    const track = findTrack();
    if (!stage || !track) {
      window.setTimeout(bindStage, 100);
      return;
    }
    if (stage === state.stage) return;

    state.stage = stage;
    state.track = track;

    stage.addEventListener(
      "pointerdown",
      (event) => {
        wakeUi();
        if (!event.target.closest("button") && !state.programmaticScroll) {
          setManualMode(true);
        }
      },
      { passive: true }
    );

    stage.addEventListener(
      "scroll",
      () => requestCaptionDepth(),
      { passive: true }
    );

    stage.addEventListener(
      "wheel",
      (event) => {
        wakeUi();
        if (!isMobileLayout() && Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
          stage.scrollLeft += event.deltaY;
          event.preventDefault();
        }
        if (!state.programmaticScroll) setManualMode(true);
      },
      { passive: false }
    );

    stage.addEventListener("keydown", (event) => {
      wakeUi();
      const amount = Math.max(160, stage.clientWidth * 0.36);
      if (event.key === "ArrowLeft") {
        stage.scrollBy({ left: -amount, behavior: "smooth" });
        setManualMode(true);
        event.preventDefault();
      } else if (event.key === "ArrowRight") {
        stage.scrollBy({ left: amount, behavior: "smooth" });
        setManualMode(true);
        event.preventDefault();
      } else if (event.key === "Home") {
        stage.scrollTo({ left: 0, behavior: "smooth" });
        setManualMode(true);
        event.preventDefault();
      } else if (event.key === "End") {
        stage.scrollTo({ left: stage.scrollWidth, behavior: "smooth" });
        setManualMode(true);
        event.preventDefault();
      }
    });

    requestCaptionDepth();
  }

  function beforeGenerate(args) {
    body().classList.remove("unicalli-complete");
    body().classList.add("unicalli-writing");
    setManualMode(false);
    setMobileComposerOpen(false);
    setMobileSettingsOpen(false);
    startTimer("准备中");
    return args;
  }

  function enterEdit() {
    body().classList.remove(
      "unicalli-writing",
      "unicalli-complete",
      "unicalli-ui-dormant"
    );
    setManualMode(false);
    setMobileSettingsOpen(false);
    setMobileComposerOpen(true);
    window.setTimeout(() => {
      const input = document.querySelector("#text-input textarea");
      if (input) input.focus();
    }, 220);
  }

  function setTheme(mode) {
    root().dataset.unicalliTheme = mode === "砚黑" ? "inkstone" : "paper";
    requestCaptionDepth();
  }

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (_) {
      // Fullscreen is optional and browser-controlled.
    }
  }

  function setNativeValue(input, value) {
    const prototype =
      input instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function restoreRerollRequest(index) {
    const article = segmentNode(index);
    if (!article) return;
    setSegmentState(index, "is-complete", "墨定");
    const reroll = article.querySelector(".segment-reroll");
    if (reroll) reroll.disabled = false;
  }

  function triggerReroll(index) {
    if (body().classList.contains("unicalli-writing")) return;
    if (state.rerollPendingIndex !== null) return;

    const article = segmentNode(index);
    const input = document.querySelector(
      "#reroll-target input, #reroll-target textarea"
    );
    if (!article || !input || !article.classList.contains("is-complete")) return;

    state.lastRerollIndex = Number(index);
    state.rerollPendingIndex = Number(index);
    setSegmentState(index, "is-reroll-requested", "准备重写");
    const reroll = article.querySelector(".segment-reroll");
    if (reroll) reroll.disabled = true;
    followIndex(index, true);

    const requestToken = `${Number(index)}:${Date.now()}`;
    setNativeValue(input, requestToken);

    state.rerollBridgeTimer = window.setTimeout(() => {
      if (state.rerollPendingIndex === Number(index)) {
        restoreRerollRequest(index);
        clearRerollBridge();
      }
    }, 30000);
  }

  function isHanCharacter(character) {
    try {
      return /^\p{Script=Han}$/u.test(character);
    } catch (_) {
      const code = character.codePointAt(0) || 0;
      return (
        (code >= 0x3400 && code <= 0x4dbf) ||
        (code >= 0x4e00 && code <= 0x9fff) ||
        (code >= 0xf900 && code <= 0xfaff) ||
        (code >= 0x20000 && code <= 0x2fa1f) ||
        (code >= 0x30000 && code <= 0x323af)
      );
    }
  }

  function hanOnly(value) {
    return Array.from(String(value || "")).filter(isHanCharacter).join("");
  }

  function bindTextInputFilter() {
    if (state.inputBound) return;
    const input = document.querySelector("#text-input textarea");
    if (!input) {
      window.setTimeout(bindTextInputFilter, 100);
      return;
    }

    state.inputBound = true;
    let composing = false;
    let filtering = false;

    input.addEventListener("compositionstart", () => {
      composing = true;
    });
    input.addEventListener("compositionend", () => {
      composing = false;
      const clean = hanOnly(input.value);
      if (clean !== input.value) setNativeValue(input, clean);
    });
    input.addEventListener("beforeinput", (event) => {
      if (composing || !event.data || !event.inputType.startsWith("insert")) return;
      if (hanOnly(event.data) !== event.data) event.preventDefault();
    });
    input.addEventListener("input", () => {
      if (composing || filtering) return;
      const clean = hanOnly(input.value);
      if (clean === input.value) return;
      filtering = true;
      setNativeValue(input, clean);
      filtering = false;
    });
  }

  function scheduleUiRest() {
    window.clearTimeout(state.idleTimer);
    body().classList.remove("unicalli-ui-dormant");
    if (
      !body().classList.contains("unicalli-writing") &&
      !body().classList.contains("unicalli-complete")
    ) return;
    state.idleTimer = window.setTimeout(() => {
      body().classList.add("unicalli-ui-dormant");
    }, 2800);
  }

  function wakeUi() {
    body().classList.remove("unicalli-ui-dormant");
    scheduleUiRest();
  }

  document.addEventListener("click", (event) => {
    wakeUi();

    const rerollButton = event.target.closest(".segment-reroll");
    if (rerollButton) {
      event.preventDefault();
      event.stopPropagation();
      const index = Number(rerollButton.dataset.rerollIndex);
      if (Number.isFinite(index)) triggerReroll(index);
      return;
    }

    // 移动端展开/收起按钮改用事件委托绑定在 document 上，而不是在创建按钮时
    // 直接 addEventListener：composer-dock 是 Gradio 管理的 Column，Gradio 自身
    // 的 Svelte 重渲染在任何时候都可能整体替换其子节点（包括我们注入的
    // .mobile-composer-bar），这会让直接绑在旧节点上的监听器随之失效，
    // 表现为按钮还在、样式正常，但点击毫无反应。委托到 document 后，只要按钮
    // 的 class 名还在（哪怕是 Gradio 重渲染后新生成的同名节点），点击就能命中。
    const composerToggle = event.target.closest(".mobile-composer-toggle");
    if (composerToggle) {
      setMobileSettingsOpen(false);
      setMobileComposerOpen(!state.mobileComposerOpen);
      return;
    }
    const settingsToggle = event.target.closest(".mobile-settings-toggle");
    if (settingsToggle) {
      setMobileComposerOpen(false);
      setMobileSettingsOpen(!state.mobileSettingsOpen);
      return;
    }
  }, true);

  ["pointermove", "touchstart", "keydown"].forEach((eventName) => {
    document.addEventListener(eventName, wakeUi, { passive: true });
  });
  window.addEventListener("resize", syncResponsiveUi, { passive: true });
  document.addEventListener("fullscreenchange", wakeUi);

  window.UniCalli = window.UniCalliV4 = {
    applyEvent,
    beforeGenerate,
    enterEdit,
    setTheme,
    followCurrent: () => {
      setManualMode(false);
      followCurrent(true);
    },
    triggerReroll,
    toggleFullscreen,
  };

  root().dataset.unicalliTheme = "paper";
  bindStage();
  bindTextInputFilter();
  ensureMobileControls();
  stopTimer("静候", false);
})();
