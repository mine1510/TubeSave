(() => {
  const BTN_ID = "tubesave-ym-btn";
  const FLOAT_ID = "tubesave-ym-float";

  function trackUrlFromPage() {
    const href = location.href.split("?")[0].split("#")[0];
    // album/123/track/456 or /track/456
    if (/\/track\/\d+/i.test(href) || /\/album\/\d+\/track\/\d+/i.test(href)) {
      return href;
    }
    // Player bar sometimes keeps track id in data attributes / canonical
    const canonical = document.querySelector("link[rel='canonical']")?.href;
    if (canonical && /music\.yandex\./i.test(canonical) && /\/track\//i.test(canonical)) {
      return canonical.split("?")[0];
    }
    // Fallback: try to build from pathname pieces
    const m = location.pathname.match(/\/(?:album\/\d+\/)?track\/(\d+)/i);
    if (m) {
      return `${location.origin}/track/${m[1]}`;
    }
    return href;
  }

  function looksLikeTrackPage() {
    return /\/track\/\d+/i.test(location.pathname) || /\/album\/\d+\/track\/\d+/i.test(location.pathname);
  }

  function styleInline(btn) {
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = "TubeSave";
    btn.title = "Скачать трек в TubeSave (аудио)";
    Object.assign(btn.style, {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      height: "32px",
      padding: "0 12px",
      marginLeft: "8px",
      border: "none",
      borderRadius: "16px",
      cursor: "pointer",
      fontFamily: "YS Text, Arial, sans-serif",
      fontSize: "13px",
      fontWeight: "500",
      color: "#fff",
      background: "#2F6FED",
      whiteSpace: "nowrap",
      userSelect: "none",
      zIndex: "9999",
    });
  }

  function styleFloat(btn) {
    btn.id = FLOAT_ID;
    btn.type = "button";
    btn.textContent = "↓ Музыка";
    btn.title = "Скачать трек в TubeSave";
    Object.assign(btn.style, {
      position: "fixed",
      right: "18px",
      bottom: "110px",
      zIndex: "2147483646",
      height: "44px",
      padding: "0 16px",
      border: "none",
      borderRadius: "22px",
      cursor: "pointer",
      fontFamily: "YS Text, Arial, sans-serif",
      fontSize: "14px",
      fontWeight: "600",
      color: "#fff",
      background: "#2F6FED",
      boxShadow: "0 8px 24px rgba(0,0,0,.28)",
      userSelect: "none",
    });
  }

  function flash(btn, text, bg) {
    const prev = btn.textContent;
    const prevBg = btn.style.background;
    btn.textContent = text;
    btn.style.background = bg;
    setTimeout(() => {
      btn.textContent = prev;
      btn.style.background = prevBg;
    }, 1400);
  }

  async function onClick(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    const btn = ev.currentTarget;
    btn.disabled = true;
    const url = trackUrlFromPage();
    if (window.TubeSaveLaunchProtocol) {
      window.TubeSaveLaunchProtocol(url, true, "best");
    }
    flash(btn, "Запуск…", "#C45C26");
    try {
      const response = await chrome.runtime.sendMessage({
        type: "tubesave-download",
        url,
        audio_only: true,
        protocol_fired: true,
      });
      if (response && response.ok) {
        flash(btn, "Отправлено", "#1B7F4B");
      } else {
        flash(btn, "Ошибка", "#B00020");
        console.warn(response && response.error);
      }
    } catch (err) {
      flash(btn, "Ошибка", "#B00020");
      console.warn(err);
    } finally {
      btn.disabled = false;
    }
  }

  function findHost() {
    return (
      document.querySelector(".page-track__actions") ||
      document.querySelector("[class*='TrackPage'][class*='Actions'], [class*='TrackPage'] [class*='Actions']") ||
      document.querySelector(".sidebar-track__actions") ||
      document.querySelector(".entity-actions") ||
      document.querySelector(".d-track__actions") ||
      null
    );
  }

  function ensureButtons() {
    if (!looksLikeTrackPage()) {
      // Still show float on music site so user can grab current player track URL if possible
      if (!document.getElementById(FLOAT_ID)) {
        const fab = document.createElement("button");
        styleFloat(fab);
        fab.addEventListener("click", onClick);
        document.documentElement.appendChild(fab);
      }
      return;
    }

    const host = findHost();
    if (host && !document.getElementById(BTN_ID)) {
      const btn = document.createElement("button");
      styleInline(btn);
      btn.addEventListener("click", onClick);
      host.appendChild(btn);
    }

    if (!document.getElementById(FLOAT_ID)) {
      const fab = document.createElement("button");
      styleFloat(fab);
      fab.addEventListener("click", onClick);
      document.documentElement.appendChild(fab);
    }
  }

  ensureButtons();
  const observer = new MutationObserver(() => ensureButtons());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      document.getElementById(BTN_ID)?.remove();
      // keep float
      ensureButtons();
    }
  }, 700);
})();
