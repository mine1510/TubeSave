(() => {
  const QUALITIES = [
    { code: "best", label: "Лучшее" },
    { code: "2160", label: "4K" },
    { code: "1440", label: "1440p" },
    { code: "1080", label: "1080p" },
    { code: "720", label: "720p" },
    { code: "480", label: "480p" },
    { code: "360", label: "360p" },
  ];

  let openMenu = null;

  function closeMenu() {
    if (openMenu) {
      openMenu.remove();
      openMenu = null;
    }
  }

  function flash(btn, text, bg) {
    const prev = btn.dataset.tsLabel || btn.textContent;
    const prevBg = btn.dataset.tsBg || btn.style.background;
    btn.textContent = text;
    btn.style.background = bg;
    setTimeout(() => {
      btn.textContent = prev;
      btn.style.background = prevBg;
    }, 1400);
  }

  async function sendDownload(url, audioOnly, quality) {
    return chrome.runtime.sendMessage({
      type: "tubesave-download",
      url,
      audio_only: Boolean(audioOnly),
      quality: quality || "best",
    });
  }

  function itemStyle(base) {
    Object.assign(base.style, {
      display: "block",
      width: "100%",
      boxSizing: "border-box",
      border: "none",
      background: "transparent",
      color: "#F4F4F5",
      textAlign: "left",
      padding: "8px 12px",
      fontFamily: "Segoe UI, Arial, sans-serif",
      fontSize: "13px",
      cursor: "pointer",
      borderRadius: "8px",
    });
    base.addEventListener("mouseenter", () => {
      base.style.background = "#2F6FED";
    });
    base.addEventListener("mouseleave", () => {
      base.style.background = "transparent";
    });
    return base;
  }

  function showMenu(anchor, getUrl) {
    closeMenu();
    const menu = document.createElement("div");
    menu.setAttribute("data-tubesave-menu", "1");
    Object.assign(menu.style, {
      position: "fixed",
      zIndex: "2147483647",
      minWidth: "220px",
      maxWidth: "260px",
      padding: "10px",
      borderRadius: "14px",
      background: "#1C1B1A",
      color: "#F4F4F5",
      boxShadow: "0 16px 40px rgba(0,0,0,.4)",
      fontFamily: "Segoe UI, Arial, sans-serif",
      fontSize: "13px",
    });

    const title = document.createElement("div");
    title.textContent = "Скачать видео";
    Object.assign(title.style, {
      fontWeight: "600",
      fontSize: "13px",
      margin: "2px 4px 8px",
    });
    menu.appendChild(title);

    const formatRow = document.createElement("div");
    Object.assign(formatRow.style, {
      display: "flex",
      gap: "6px",
      margin: "0 4px 10px",
    });
    let format = "mp4";
    function formatChip(code, label) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.textContent = label;
      Object.assign(chip.style, {
        flex: "1",
        border: "none",
        borderRadius: "10px",
        padding: "7px 8px",
        cursor: "pointer",
        fontWeight: "600",
        fontSize: "12px",
        color: "#fff",
        background: code === format ? "#2F6FED" : "#2A2928",
      });
      chip.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        format = code;
        Array.from(formatRow.children).forEach((child) => {
          child.style.background = child === chip ? "#2F6FED" : "#2A2928";
        });
        qualityBlock.style.opacity = format === "aac" ? "0.45" : "1";
        qualityBlock.style.pointerEvents = format === "aac" ? "none" : "auto";
      });
      return chip;
    }
    formatRow.appendChild(formatChip("mp4", "MP4"));
    formatRow.appendChild(formatChip("aac", "AAC"));
    menu.appendChild(formatRow);

    const qualityBlock = document.createElement("div");
    const qTitle = document.createElement("div");
    qTitle.textContent = "Качество";
    Object.assign(qTitle.style, {
      color: "#A8A29E",
      fontSize: "11px",
      margin: "0 4px 6px",
    });
    qualityBlock.appendChild(qTitle);

    QUALITIES.forEach((q) => {
      const btn = itemStyle(document.createElement("button"));
      btn.type = "button";
      btn.textContent = q.label;
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const url = getUrl();
        closeMenu();
        try {
          const response = await sendDownload(url, format === "aac", q.code);
          if (response && response.ok) {
            flash(anchor, "Отправлено", "#1B7F4B");
          } else {
            flash(anchor, "Ошибка", "#B00020");
            console.warn(response && response.error);
          }
        } catch (err) {
          flash(anchor, "Ошибка", "#B00020");
          console.warn(err);
        }
      });
      qualityBlock.appendChild(btn);
    });
    menu.appendChild(qualityBlock);

    const aacHint = document.createElement("div");
    aacHint.textContent = "AAC — только звук (M4A)";
    Object.assign(aacHint.style, {
      color: "#A8A29E",
      fontSize: "11px",
      margin: "8px 4px 2px",
    });
    menu.appendChild(aacHint);

    document.documentElement.appendChild(menu);
    openMenu = menu;

    const rect = anchor.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || 220;
    const menuHeight = menu.offsetHeight || 280;
    let left = rect.left;
    let top = rect.bottom + 8;
    if (left + menuWidth > window.innerWidth - 12) {
      left = window.innerWidth - menuWidth - 12;
    }
    if (top + menuHeight > window.innerHeight - 12) {
      top = Math.max(12, rect.top - menuHeight - 8);
    }
    menu.style.left = `${Math.max(8, left)}px`;
    menu.style.top = `${Math.max(8, top)}px`;
  }

  function bindPicker(anchor, getUrl) {
    if (anchor.dataset.tsPicker === "1") {
      return;
    }
    anchor.dataset.tsPicker = "1";
    anchor.dataset.tsLabel = anchor.textContent;
    anchor.dataset.tsBg = anchor.style.background || "#2F6FED";
    anchor.title = "Скачать видео: выбрать качество и формат";
    anchor.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (openMenu) {
        closeMenu();
        return;
      }
      showMenu(anchor, getUrl);
    });
  }

  document.addEventListener("click", (ev) => {
    if (!openMenu) {
      return;
    }
    if (openMenu.contains(ev.target)) {
      return;
    }
    closeMenu();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      closeMenu();
    }
  });
  window.addEventListener("scroll", closeMenu, true);

  window.TubeSavePicker = { bindPicker, closeMenu, flash };
})();
