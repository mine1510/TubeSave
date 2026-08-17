(() => {
  const WATCH_BTN_ID = "tubesave-yt-btn";
  const SHORTS_BTN_ID = "tubesave-yt-shorts-btn";

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

  function styleWatchButton(btn) {
    btn.id = WATCH_BTN_ID;
    btn.type = "button";
    btn.textContent = "Скачать видео";
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

  function attachPicker(btn) {
    if (window.TubeSavePicker) {
      window.TubeSavePicker.bindPicker(btn, currentWatchUrl);
    }
  }

  function findWatchActionsRow() {
    return (
      document.querySelector("ytd-watch-metadata #actions") ||
      document.querySelector("ytd-watch-flexy #actions-inner") ||
      null
    );
  }

  function ensureWatchButton() {
    if (document.getElementById(WATCH_BTN_ID)) {
      return;
    }
    const host = findWatchActionsRow();
    if (!host) {
      return;
    }
    const btn = document.createElement("button");
    styleWatchButton(btn);
    attachPicker(btn);
    host.appendChild(btn);
  }

  function findActiveShortsActions() {
    const active =
      document.querySelector("ytd-reel-video-renderer[is-active]") ||
      document.querySelector("ytd-reel-video-renderer[is-active='']");
    if (active) {
      const actions =
        active.querySelector("#actions.ytd-reel-player-overlay-renderer") ||
        active.querySelector("ytd-reel-player-overlay-renderer #actions") ||
        active.querySelector("#actions");
      if (actions) {
        return actions;
      }
    }
    return (
      document.querySelector("ytd-reel-player-overlay-renderer #actions") ||
      document.querySelector("#shorts-inner-container ytd-reel-player-overlay-renderer #actions") ||
      null
    );
  }

  function findLikeInActions(host) {
    return (
      host.querySelector(":scope > #like-button") ||
      host.querySelector(":scope > like-button-view-model") ||
      host.querySelector("#like-button") ||
      host.querySelector("like-button-view-model") ||
      null
    );
  }

  function createShortsAction() {
    const wrap = document.createElement("div");
    wrap.id = SHORTS_BTN_ID;
    wrap.title = "Скачать Shorts: выбрать качество и формат";
    wrap.setAttribute("aria-label", "Скачать Shorts");
    wrap.style.setProperty("display", "flex", "important");
    wrap.style.setProperty("flex-direction", "column", "important");
    wrap.style.setProperty("align-items", "center", "important");
    wrap.style.setProperty("justify-content", "center", "important");
    wrap.style.setProperty("width", "48px", "important");
    wrap.style.setProperty("margin", "12px 0 0 0", "important");
    wrap.style.setProperty("pointer-events", "auto", "important");
    wrap.style.setProperty("z-index", "30", "important");
    wrap.style.setProperty("position", "relative", "important");
    wrap.style.setProperty("flex", "0 0 auto", "important");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">' +
      '<path fill="#fff" d="M12 3v10.2l3.4-3.4 1.4 1.4L12 16.4 7.2 11.6l1.4-1.4L11 13.2V3h1zm-7 16h14v2H5v-2z"/></svg>';
    applyBase(btn);
    btn.style.setProperty("width", "48px", "important");
    btn.style.setProperty("height", "48px", "important");
    btn.style.setProperty("min-width", "48px", "important");
    btn.style.setProperty("border-radius", "50%", "important");
    btn.style.setProperty("padding", "0", "important");
    btn.style.setProperty("box-shadow", "0 2px 8px rgba(0,0,0,.35)", "important");

    const caption = document.createElement("span");
    caption.textContent = "Скачать";
    caption.style.setProperty("display", "block", "important");
    caption.style.setProperty("margin-top", "6px", "important");
    caption.style.setProperty("font-family", "Roboto, Arial, sans-serif", "important");
    caption.style.setProperty("font-size", "12px", "important");
    caption.style.setProperty("font-weight", "500", "important");
    caption.style.setProperty("color", "#fff", "important");
    caption.style.setProperty("text-align", "center", "important");
    caption.style.setProperty("line-height", "1.2", "important");
    caption.style.setProperty("text-shadow", "0 1px 2px rgba(0,0,0,.6)", "important");

    wrap.appendChild(btn);
    wrap.appendChild(caption);
    wrap.style.setProperty("cursor", "pointer", "important");
    attachPicker(wrap);
    return wrap;
  }

  function placeShortsButton(host, wrap) {
    const like = findLikeInActions(host);
    if (like && wrap.nextElementSibling !== like) {
      host.insertBefore(wrap, like);
      return;
    }
    if (wrap.parentElement !== host) {
      if (like) {
        host.insertBefore(wrap, like);
      } else {
        host.insertBefore(wrap, host.firstChild);
      }
    }
  }

  function ensureShortsButton() {
    const host = findActiveShortsActions();
    if (!host) {
      return;
    }
    let wrap = document.getElementById(SHORTS_BTN_ID);
    if (!wrap) {
      wrap = createShortsAction();
    }
    placeShortsButton(host, wrap);
  }

  function clearShortsButton() {
    document.getElementById(SHORTS_BTN_ID)?.remove();
  }

  function clearWatchButton() {
    document.getElementById(WATCH_BTN_ID)?.remove();
  }

  function ensureButton() {
    if (isShortsPage()) {
      clearWatchButton();
      ensureShortsButton();
      return;
    }
    clearShortsButton();
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
      document.getElementById(SHORTS_BTN_ID)?.remove();
    }
    ensureButton();
  }, 500);
})();
