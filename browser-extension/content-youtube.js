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

  function styleInline(btn, label) {
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = label;
    btn.title = "Скачать видео: выбрать качество и формат";
    btn.setAttribute("aria-label", "Скачать видео");
    Object.assign(btn.style, {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      height: "36px",
      padding: "0 14px",
      marginLeft: "8px",
      border: "none",
      borderRadius: "18px",
      cursor: "pointer",
      fontFamily: "Roboto, Arial, sans-serif",
      fontSize: "14px",
      fontWeight: "500",
      color: "#fff",
      background: "#2F6FED",
      whiteSpace: "nowrap",
      userSelect: "none",
      zIndex: "9999",
    });
  }

  function styleFloat(btn, label) {
    btn.id = FLOAT_ID;
    btn.type = "button";
    btn.textContent = label;
    btn.title = "Скачать Shorts: выбрать качество и формат";
    Object.assign(btn.style, {
      position: "fixed",
      right: "18px",
      bottom: "96px",
      zIndex: "2147483646",
      height: "44px",
      padding: "0 16px",
      border: "none",
      borderRadius: "22px",
      cursor: "pointer",
      fontFamily: "Roboto, Arial, sans-serif",
      fontSize: "14px",
      fontWeight: "600",
      color: "#fff",
      background: "#2F6FED",
      boxShadow: "0 8px 24px rgba(0,0,0,.28)",
      userSelect: "none",
    });
  }

  function attachPicker(btn) {
    if (window.TubeSavePicker) {
      window.TubeSavePicker.bindPicker(btn, currentWatchUrl);
    }
  }

  function findActionsRow() {
    if (isShortsPage()) {
      return (
        document.querySelector("ytd-reel-player-overlay-renderer #actions") ||
        document.querySelector("#actions.ytd-reel-player-overlay-renderer") ||
        document.querySelector("ytd-shorts #actions") ||
        document.querySelector("#like-button")?.parentElement ||
        null
      );
    }
    return (
      document.querySelector("ytd-watch-metadata #actions") ||
      document.querySelector("#actions-inner") ||
      document.querySelector("#menu-container") ||
      null
    );
  }

  function ensureInlineButton() {
    if (document.getElementById(BTN_ID)) {
      return true;
    }
    const host = findActionsRow();
    if (!host) {
      return false;
    }
    const btn = document.createElement("button");
    styleInline(btn, isShortsPage() ? "Скачать Shorts" : "Скачать видео");
    attachPicker(btn);
    host.appendChild(btn);
    return true;
  }

  function ensureFloatButton() {
    if (!isShortsPage()) {
      const old = document.getElementById(FLOAT_ID);
      if (old) old.remove();
      return;
    }
    if (document.getElementById(FLOAT_ID)) {
      return;
    }
    const btn = document.createElement("button");
    styleFloat(btn, "↓ Shorts");
    attachPicker(btn);
    document.documentElement.appendChild(btn);
  }

  function ensureButton() {
    const placed = ensureInlineButton();
    if (!placed && isShortsPage()) {
      ensureFloatButton();
    } else if (placed) {
      const old = document.getElementById(FLOAT_ID);
      if (old) old.remove();
    } else {
      ensureFloatButton();
    }
  }

  ensureButton();
  const observer = new MutationObserver(() => ensureButton());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      document.getElementById(BTN_ID)?.remove();
      document.getElementById(FLOAT_ID)?.remove();
      if (window.TubeSavePicker) window.TubeSavePicker.closeMenu();
      ensureButton();
    }
  }, 700);
})();
