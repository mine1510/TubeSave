(() => {
  const BTN_ID = "tubesave-yt-btn";
  const FLOAT_ID = "tubesave-yt-float";

  function isShortsPage() {
    return /\/shorts\//i.test(location.pathname);
  }

  function currentWatchUrl() {
    const href = location.href.split("&")[0].split("#")[0];
    if (/\/watch\?/.test(href) || /\/shorts\//.test(href)) {
      return href;
    }
    const m = location.pathname.match(/\/shorts\/([A-Za-z0-9_-]+)/);
    if (m) {
      return `https://www.youtube.com/shorts/${m[1]}`;
    }
    return href;
  }

  function applyBase(el) {
    el.style.setProperty("all", "unset", "important");
    el.style.setProperty("box-sizing", "border-box", "important");
    el.style.setProperty("display", "inline-flex", "important");
    el.style.setProperty("align-items", "center", "important");
    el.style.setProperty("justify-content", "center", "important");
    el.style.setProperty("cursor", "pointer", "important");
    el.style.setProperty("font-family", "Roboto, Arial, sans-serif", "important");
    el.style.setProperty("font-weight", "600", "important");
    el.style.setProperty("color", "#fff", "important");
    el.style.setProperty("background", "#2F6FED", "important");
    el.style.setProperty("border", "none", "important");
    el.style.setProperty("white-space", "nowrap", "important");
    el.style.setProperty("user-select", "none", "important");
    el.style.setProperty("pointer-events", "auto", "important");
    el.style.setProperty("visibility", "visible", "important");
    el.style.setProperty("opacity", "1", "important");
  }

  function styleInline(btn, label) {
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = label;
    btn.title = "Скачать видео: выбрать качество и формат";
    btn.setAttribute("aria-label", "Скачать видео");
    applyBase(btn);
    btn.style.setProperty("height", "36px", "important");
    btn.style.setProperty("padding", "0 14px", "important");
    btn.style.setProperty("margin-left", "8px", "important");
    btn.style.setProperty("border-radius", "18px", "important");
    btn.style.setProperty("font-size", "14px", "important");
    btn.style.setProperty("z-index", "9999", "important");
  }

  function styleFloat(btn, label) {
    btn.id = FLOAT_ID;
    btn.type = "button";
    btn.textContent = label;
    btn.title = "Скачать Shorts: выбрать качество и формат";
    btn.setAttribute("aria-label", "Скачать Shorts");
    applyBase(btn);
    btn.style.setProperty("position", "fixed", "important");
    btn.style.setProperty("top", "72px", "important");
    btn.style.setProperty("right", "16px", "important");
    btn.style.setProperty("bottom", "auto", "important");
    btn.style.setProperty("left", "auto", "important");
    btn.style.setProperty("z-index", "2147483646", "important");
    btn.style.setProperty("height", "44px", "important");
    btn.style.setProperty("min-width", "132px", "important");
    btn.style.setProperty("padding", "0 16px", "important");
    btn.style.setProperty("border-radius", "22px", "important");
    btn.style.setProperty("font-size", "14px", "important");
    btn.style.setProperty("box-shadow", "0 8px 24px rgba(0,0,0,.35)", "important");
  }

  function attachPicker(btn) {
    if (window.TubeSavePicker) {
      window.TubeSavePicker.bindPicker(btn, currentWatchUrl);
    }
  }

  function findWatchActionsRow() {
    return (
      document.querySelector("ytd-watch-metadata #actions") ||
      document.querySelector("#actions-inner") ||
      document.querySelector("#menu-container") ||
      null
    );
  }

  function ensureWatchButton() {
    if (document.getElementById(BTN_ID)) {
      return;
    }
    const host = findWatchActionsRow();
    if (!host) {
      return;
    }
    const btn = document.createElement("button");
    styleInline(btn, "Скачать видео");
    attachPicker(btn);
    host.appendChild(btn);
  }

  function ensureShortsFloat() {
    if (document.getElementById(FLOAT_ID)) {
      return;
    }
    const btn = document.createElement("button");
    styleFloat(btn, "Скачать Shorts");
    attachPicker(btn);
    (document.body || document.documentElement).appendChild(btn);
  }

  function clearShortsFloat() {
    document.getElementById(FLOAT_ID)?.remove();
  }

  function clearWatchButton() {
    document.getElementById(BTN_ID)?.remove();
  }

  function ensureButton() {
    if (isShortsPage()) {
      clearWatchButton();
      ensureShortsFloat();
      return;
    }
    clearShortsFloat();
    ensureWatchButton();
  }

  ensureButton();
  const observer = new MutationObserver(() => ensureButton());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      if (window.TubeSavePicker) window.TubeSavePicker.closeMenu();
    }
    ensureButton();
  }, 500);
})();
