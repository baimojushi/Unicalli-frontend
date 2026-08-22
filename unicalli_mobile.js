(() => {
  if (window.UniCalliMobileV1) return;

  const root = document.documentElement;
  const media = window.matchMedia("(max-width: 767px)");
  const state = {
    mode: "compose",
    segments: new Map(),
    completed: new Set(),
    activeIndex: null,
    rerollPending: null,
    inputBound: false,
  };

  const q = (selector) => document.querySelector(selector);

  function active() {
    return media.matches;
  }

  function syncViewportHeight() {
    if (!active()) {
      root.style.removeProperty("--m-viewport-height");
      return;
    }
    const height = Math.round(window.visualViewport?.height || window.innerHeight);
    root.style.setProperty("--m-viewport-height", `${height}px`);
  }

  function setMode(mode) {
    state.mode = mode;
    if (!active()) return;
    root.dataset.unicalliMobileMode = mode;
  }

  function setTheme(mode) {
    root.dataset.unicalliTheme = mode === "砚黑" ? "inkstone" : "paper";
  }

  function enterCompose() {
    setMode("compose");
    window.setTimeout(() => q("#mobile-text-input textarea")?.focus(), 80);
  }

  function openSettings() {
    setMode("settings");
  }

  function closeSettings() {
    setMode("compose");
  }

  function beforeGenerate(args) {
    if (active()) {
      clearLiveImage();
      setMode("generating");
    }
    return args;
  }

  function clearLiveImage() {
    const live = q(".mobile-live-art");
    const image = q("#mobile-live-image");
    if (image) image.removeAttribute("src");
    if (live) live.dataset.hasImage = "false";
  }

  function updateLiveCopy(title, meta) {
    const titleNode = q("#mobile-generation-title");
    const metaNode = q("#mobile-generation-meta");
    if (titleNode && title) titleNode.textContent = title;
    if (metaNode && meta) metaNode.textContent = meta;
  }

  function showLiveImage(src) {
    const live = q(".mobile-live-art");
    const image = q("#mobile-live-image");
    if (!live || !image || !src) return;
    if (image.src !== src) image.src = src;
    live.dataset.hasImage = "true";
  }

  function readingTrack() {
    return q("#mobile-reading-track");
  }

  function segmentNode(index) {
    return readingTrack()?.querySelector(
      `.mobile-sheet[data-segment-index="${Number(index)}"]`
    ) || null;
  }

  function ensureSheet(index) {
    const numeric = Number(index);
    let sheet = segmentNode(numeric);
    if (sheet) return sheet;

    const segment = state.segments.get(numeric);
    const track = readingTrack();
    if (!segment || !track) return null;

    sheet = document.createElement("article");
    sheet.className = "mobile-sheet";
    sheet.dataset.segmentIndex = String(numeric);
    sheet.innerHTML = `
      <div class="mobile-sheet-art">
        <img alt="第 ${numeric + 1} 段书法" draggable="false" />
      </div>
      <div class="mobile-sheet-meta">
        <span class="mobile-sheet-index">${String(numeric + 1).padStart(2, "0")}</span>
        <strong class="mobile-sheet-text"></strong>
        <button class="mobile-sheet-reroll" type="button" disabled>重写</button>
      </div>`;

    const text = sheet.querySelector(".mobile-sheet-text");
    if (text) text.textContent = segment.display_text || "";
    const reroll = sheet.querySelector(".mobile-sheet-reroll");
    if (reroll) {
      reroll.dataset.rerollIndex = String(numeric);
      reroll.setAttribute("aria-label", `重写第 ${numeric + 1} 段`);
    }

    const existing = Array.from(track.querySelectorAll(".mobile-sheet"));
    const before = existing.find(
      (node) => Number(node.dataset.segmentIndex) > numeric
    );
    if (before) track.insertBefore(sheet, before);
    else track.append(sheet);
    return sheet;
  }

  function completeSheet(index, src) {
    const sheet = ensureSheet(index);
    if (!sheet) return;
    const image = sheet.querySelector("img");
    if (image && src) image.src = src;
    const button = sheet.querySelector(".mobile-sheet-reroll");
    if (button) button.disabled = false;
    state.completed.add(Number(index));
  }

  function scrollToSheet(index, behavior = "smooth") {
    const sheet = segmentNode(index);
    if (!sheet) return;
    sheet.scrollIntoView({ behavior, block: "start" });
  }

  function reset(segments) {
    state.segments = new Map(
      (segments || []).map((segment) => [Number(segment.index), segment])
    );
    state.completed.clear();
    state.activeIndex = null;
    state.rerollPending = null;
    readingTrack()?.replaceChildren();
    clearLiveImage();
    updateLiveCopy("正在备纸", `${state.segments.size || 0} 段待写`);
    setMode("generating");
  }

  function setNativeValue(input, value) {
    if (!input) return;
    const proto = input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function triggerReroll(index) {
    const numeric = Number(index);
    if (!active() || state.rerollPending !== null || !state.completed.has(numeric)) return;
    const input = q("#mobile-reroll-target input, #mobile-reroll-target textarea");
    if (!input) return;

    state.rerollPending = numeric;
    const button = segmentNode(numeric)?.querySelector(".mobile-sheet-reroll");
    if (button) button.disabled = true;
    updateLiveCopy(`第 ${numeric + 1} 段`, "准备重写");
    const oldImage = segmentNode(numeric)?.querySelector("img")?.src;
    if (oldImage) showLiveImage(oldImage);
    setMode("generating");
    setNativeValue(input, `${numeric}:${Date.now()}`);
  }

  function applyEvent(event) {
    if (!event || typeof event !== "object") return;
    const kind = event.kind;
    const index = Number(event.index);

    if (kind === "reset") {
      reset(event.segments || []);
      return;
    }

    if (kind === "task_started") {
      setMode("generating");
      updateLiveCopy("正在落笔", "墨色开始铺展");
      return;
    }

    if (kind === "segment_started") {
      state.activeIndex = index;
      clearLiveImage();
      const segment = state.segments.get(index);
      updateLiveCopy(
        segment?.display_text || `第 ${index + 1} 段`,
        `第 ${index + 1} / ${state.segments.size} 段 · 候墨`
      );
      return;
    }

    if (kind === "preview") {
      state.activeIndex = index;
      showLiveImage(event.image);
      const current = Math.min(Number(event.step || 0) + 1, Number(event.total_steps || 1));
      updateLiveCopy(
        state.segments.get(index)?.display_text || `第 ${index + 1} 段`,
        `显墨 ${current} / ${Number(event.total_steps || 1)}`
      );
      return;
    }

    if (kind === "segment_completed") {
      state.activeIndex = index;
      completeSheet(index, event.image);
      showLiveImage(event.image);
      updateLiveCopy(
        state.segments.get(index)?.display_text || `第 ${index + 1} 段`,
        `第 ${index + 1} 段 · 墨定`
      );
      return;
    }

    if (kind === "task_completed") {
      state.rerollPending = null;
      setMode("reading");
      window.requestAnimationFrame(() => {
        const track = readingTrack();
        if (track) track.scrollTop = 0;
      });
      return;
    }

    if (kind === "task_error") {
      state.rerollPending = null;
      setMode(state.completed.size ? "reading" : "compose");
      return;
    }

    if (kind === "reroll_started") {
      state.rerollPending = index;
      state.activeIndex = index;
      updateLiveCopy(`第 ${index + 1} 段`, "重新运笔");
      setMode("generating");
      return;
    }

    if (kind === "reroll_preview") {
      showLiveImage(event.image);
      const current = Math.min(Number(event.step || 0) + 1, Number(event.total_steps || 1));
      updateLiveCopy(`第 ${index + 1} 段`, `重写显墨 ${current} / ${Number(event.total_steps || 1)}`);
      return;
    }

    if (kind === "reroll_completed") {
      completeSheet(index, event.image);
      state.rerollPending = null;
      setMode("reading");
      window.requestAnimationFrame(() => scrollToSheet(index, "auto"));
      return;
    }

    if (kind === "reroll_error") {
      const button = segmentNode(index)?.querySelector(".mobile-sheet-reroll");
      if (button) button.disabled = false;
      state.rerollPending = null;
      setMode("reading");
      window.requestAnimationFrame(() => scrollToSheet(index, "auto"));
    }
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
    const input = q("#mobile-text-input textarea");
    if (!input) {
      window.setTimeout(bindTextInputFilter, 100);
      return;
    }

    state.inputBound = true;
    let composing = false;
    let filtering = false;

    input.addEventListener("compositionstart", () => { composing = true; });
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

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".mobile-sheet-reroll");
    if (!button) return;
    event.preventDefault();
    const index = Number(button.dataset.rerollIndex);
    if (Number.isFinite(index)) triggerReroll(index);
  }, true);

  function activate() {
    if (active()) {
      syncViewportHeight();
      root.dataset.unicalliMobileMode = state.mode;
      bindTextInputFilter();
    } else {
      root.removeAttribute("data-unicalli-mobile-mode");
    }
  }

  if (media.addEventListener) media.addEventListener("change", activate);
  else media.addListener?.(activate);
  window.addEventListener("resize", syncViewportHeight, { passive: true });
  window.visualViewport?.addEventListener("resize", syncViewportHeight, { passive: true });

  window.UniCalliMobile = window.UniCalliMobileV1 = {
    applyEvent,
    beforeGenerate,
    enterCompose,
    openSettings,
    closeSettings,
    setTheme,
    triggerReroll,
  };

  activate();
  window.setTimeout(activate, 120);
})();
