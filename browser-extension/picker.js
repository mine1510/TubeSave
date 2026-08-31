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
    const caption = btn.querySelector("span") || btn;
    const prev = btn.dataset.tsLabel || caption.textContent;
    const prevBg = btn.dataset.tsBg || btn.style.background;
    caption.textContent = text;
    const paintTarget = btn.querySelector("button") || btn;
    paintTarget.style.background = bg;
    setTimeout(() => {
      caption.textContent = prev;
      paintTarget.style.background = prevBg;
    }, 1400);
  }

  async function sendDownload(url, audioOnly, quality, audioFormat) {
    return chrome.runtime.sendMessage({
      type: "tubesave-download",
      url,
      audio_only: Boolean(audioOnly),
      quality: quality || "best",
      audio_format: audioFormat || "",
      protocol_fired: true,
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

    function startDownload(audioOnly, quality, audioFormat) {
      const url = getUrl();
      closeMenu();
      if (!url) {
        flash(anchor, "Нет ссылки", "#B00020");
        return;
      }
      if (window.TubeSaveLaunchProtocol) {
        window.TubeSaveLaunchProtocol(url, audioOnly, quality, audioFormat);
      }
      flash(anchor, "Запуск…", "#C45C26");
      sendDownload(url, audioOnly, quality, audioFormat)
        .then((response) => {
          if (response && response.ok) {
            const okLabel = audioOnly ? (audioFormat === "mp3" ? "MP3" : "AAC") : "Отправлено";
            flash(anchor, okLabel, "#1B7F4B");
          } else {
            flash(anchor, "Ошибка", "#B00020");
            console.warn(response && response.error);
          }
        })
        .catch((err) => {
          flash(anchor, "Ошибка", "#B00020");
          console.warn(err);
        });
    }

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
        background: code === "mp4" ? "#2F6FED" : "#C45C26",
      });
      chip.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (code === "aac") {
          startDownload(true, "best", "aac");
          return;
        }
        if (code === "mp3") {
          startDownload(true, "best", "mp3");
          return;
        }
        format = "mp4";
      });
      return chip;
    }
    formatRow.appendChild(formatChip("mp4", "MP4"));
    formatRow.appendChild(formatChip("aac", "AAC"));
    formatRow.appendChild(formatChip("mp3", "MP3"));
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
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        startDownload(format === "aac", q.code);
      });
      qualityBlock.appendChild(btn);
    });
    menu.appendChild(qualityBlock);

    const aacHint = document.createElement("div");
    aacHint.textContent = "AAC — звук M4A. MP3 перекодируется из AAC.";
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
    const caption = anchor.querySelector("span");
    anchor.dataset.tsPicker = "1";
    anchor.dataset.tsLabel = caption ? caption.textContent : anchor.textContent;
    const paintTarget = anchor.querySelector("button") || anchor;
    anchor.dataset.tsBg = paintTarget.style.background || "#2F6FED";
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
