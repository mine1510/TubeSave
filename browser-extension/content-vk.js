(() => {
  const BTN_ID = "tubesave-vk-btn";
  const FLOAT_ID = "tubesave-vk-float";

  function isClipPage() {
    const path = location.pathname.toLowerCase();
    return (
      path.includes("/clip") ||
      path.includes("/clips") ||
      /clip-?\d/i.test(path) ||
      location.search.includes("z=clip")
    );
  }

  function isVideoPage() {
    const path = location.pathname.toLowerCase();
    return (
      isClipPage() ||
      path.includes("/video") ||
      /video-?\d/i.test(path) ||
      location.search.includes("z=video")
    );
  }

  function currentMediaUrl() {
    // Prefer canonical share URL when VK embeds z=clip / z=video in query.
    const z = new URLSearchParams(location.search).get("z");
    if (z && /^(clip|video)/i.test(z)) {
      const id = z.split("/")[0];
      return `${location.origin}/${id}`;
    }
    return location.href.split("#")[0];
  }

  function styleInline(btn, label) {
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = label;
    btn.title = "Скачать в TubeSave";
    Object.assign(btn.style, {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      height: "32px",
      padding: "0 12px",
      marginLeft: "8px",
      border: "none",
      borderRadius: "8px",
      cursor: "pointer",
      fontFamily: "VK Sans Display, Arial, sans-serif",
      fontSize: "13px",
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
    btn.title = "Скачать клип/видео в TubeSave";
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
      fontFamily: "VK Sans Display, Arial, sans-serif",
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
    try {
      const response = await chrome.runtime.sendMessage({
        type: "tubesave-download",
        url: currentMediaUrl(),
        audio_only: false,
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
      document.querySelector("[class*='VideoModalActions']") ||
      document.querySelector("[class*='vkuiInternalModalCardBase']") ||
      document.querySelector(".VideoPage__actions") ||
      document.querySelector(".videoplayer_share")?.parentElement ||
      document.querySelector("[data-testid='video_modal_header']") ||
      null
    );
  }

  function label() {
    return isClipPage() ? "TubeSave Клип" : "TubeSave";
  }

  function ensureButtons() {
    if (!isVideoPage()) {
      document.getElementById(BTN_ID)?.remove();
      document.getElementById(FLOAT_ID)?.remove();
      return;
    }

    const host = findHost();
    if (host && !document.getElementById(BTN_ID)) {
      const btn = document.createElement("button");
      styleInline(btn, label());
      btn.addEventListener("click", onClick);
      host.appendChild(btn);
    }

    if (!document.getElementById(FLOAT_ID)) {
      const fab = document.createElement("button");
      styleFloat(fab, isClipPage() ? "↓ Клип" : "↓ VK");
      fab.addEventListener("click", onClick);
      document.documentElement.appendChild(fab);
    } else {
      const fab = document.getElementById(FLOAT_ID);
      if (fab) fab.textContent = isClipPage() ? "↓ Клип" : "↓ VK";
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
      document.getElementById(FLOAT_ID)?.remove();
      ensureButtons();
    }
  }, 700);
})();
