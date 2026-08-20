(() => {
  const PANEL_ID = "tubesave-ym-panel";
  const WRAP_ID = "tubesave-ym-panel-wrap";
  const PAGE_ID = "tubesave-ym-btn";
  const FLOAT_ID = "tubesave-ym-float";
  let panelWrap = null;
  let panelBtn = null;

  function trackIdFromText(text) {
    const m = String(text || "").match(/\/track\/(\d+)/i);
    return m ? m[1] : null;
  }

  function buildTrackUrl(trackId, albumId) {
    const origin = location.origin || "https://music.yandex.ru";
    if (albumId) {
      return `${origin}/album/${albumId}/track/${trackId}`;
    }
    return `${origin}/track/${trackId}`;
  }

  function normalizeTrackHref(href) {
    const id = trackIdFromText(href);
    if (!id) {
      return null;
    }
    const albumMatch = String(href).match(/\/album\/(\d+)/i);
    return buildTrackUrl(id, albumMatch ? albumMatch[1] : null);
  }

  function trackUrlFromLocation() {
    const href = location.href.split("?")[0].split("#")[0];
    const fromHref = normalizeTrackHref(href);
    if (fromHref) {
      return fromHref;
    }

    const hash = location.hash || "";
    const fromHash = normalizeTrackHref(hash);
    if (fromHash) {
      return fromHash;
    }

    const canonical = document.querySelector("link[rel='canonical']")?.href;
    if (canonical) {
      const fromCanonical = normalizeTrackHref(canonical);
      if (fromCanonical) {
        return fromCanonical;
      }
    }

    const params = new URLSearchParams(location.search || "");
    for (const key of ["trackId", "track_id", "track"]) {
      const raw = params.get(key);
      if (raw && /^\d+$/.test(raw)) {
        const albumRaw = params.get("albumId") || params.get("album_id") || params.get("album");
        const albumId = albumRaw && /^\d+$/.test(albumRaw) ? albumRaw : null;
        return buildTrackUrl(raw, albumId);
      }
    }

    const pathMatch = location.pathname.match(/\/(?:album\/(\d+)\/)?track\/(\d+)/i);
    if (pathMatch) {
      return buildTrackUrl(pathMatch[2], pathMatch[1] || null);
    }

    return null;
  }

  function trackFromPlayerLinks() {
    const roots = [
      document.querySelector("[class*='PlayerBar']"),
      document.querySelector("[class*='player-controls']"),
      document.querySelector("[data-testid='player']"),
      document.querySelector("footer"),
    ].filter(Boolean);

    const scopes = roots.length ? roots : [document.documentElement];
    for (const root of scopes) {
      const links = root.querySelectorAll("a[href*='/track/']");
      for (const link of links) {
        const href = link.href || link.getAttribute("href") || "";
        const url = normalizeTrackHref(href);
        if (url) {
          return url;
        }
      }
    }

    const playingLink = document.querySelector(
      "[class*='playing'] a[href*='/track/'], [class*='Playing'] a[href*='/track/'], [data-testid='track-row'][aria-current='true'] a[href*='/track/']"
    );
    if (playingLink) {
      return normalizeTrackHref(playingLink.href || playingLink.getAttribute("href") || "");
    }

    return null;
  }

  function trackFromExternalApi(timeoutMs = 700) {
    return new Promise((resolve) => {
      const token = `ts-ym-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      let done = false;

      const finish = (url) => {
        if (done) {
          return;
        }
        done = true;
        window.removeEventListener("tubesave-ym-track", onEvent);
        resolve(url || null);
      };

      const onEvent = (event) => {
        const detail = event && event.detail;
        if (!detail || detail.token !== token) {
          return;
        }
        finish(detail.url || null);
      };

      window.addEventListener("tubesave-ym-track", onEvent);

      const script = document.createElement("script");
      script.textContent = `
        (function () {
          var url = null;
          try {
            var api = window.externalAPI || window.ymPlayerApi;
            if (api && typeof api.getCurrentTrack === "function") {
              var track = api.getCurrentTrack();
              if (track) {
                var id = track.id || (track.track && track.track.id);
                var albumId = track.albumId || (track.album && track.album.id);
                if (id) {
                  var origin = location.origin || "https://music.yandex.ru";
                  url = albumId
                    ? origin + "/album/" + albumId + "/track/" + id
                    : origin + "/track/" + id;
                }
              }
            }
          } catch (err) {
            console.warn("TubeSave YM API", err);
          }
          window.dispatchEvent(
            new CustomEvent("tubesave-ym-track", {
              detail: { token: "${token}", url: url },
            })
          );
        })();
      `;
      (document.documentElement || document.head).appendChild(script);
      script.remove();
      setTimeout(() => finish(null), timeoutMs);
    });
  }

  async function resolveTrackUrl() {
    const fromPlayer = trackFromPlayerLinks();
    if (fromPlayer) {
      return fromPlayer;
    }
    const fromLocation = trackUrlFromLocation();
    if (fromLocation) {
      return fromLocation;
    }
    return trackFromExternalApi();
  }

  function stylePanel(btn) {
    btn.id = PANEL_ID;
    btn.type = "button";
    btn.textContent = "↓";
    btn.title = "Скачать в TubeSave";
    btn.setAttribute("aria-label", "Скачать в TubeSave");
    Object.assign(btn.style, {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      flexShrink: "0",
      width: "36px",
      height: "36px",
      margin: "0",
      padding: "0",
      border: "none",
      borderRadius: "50%",
      cursor: "pointer",
      fontFamily: "YS Text, Arial, sans-serif",
      fontSize: "16px",
      fontWeight: "700",
      lineHeight: "1",
      color: "#fff",
      background: "#2F6FED",
      boxShadow: "0 2px 8px rgba(0,0,0,.35)",
      userSelect: "none",
      verticalAlign: "middle",
      pointerEvents: "auto",
      position: "relative",
      zIndex: "2147483647",
      touchAction: "manipulation",
    });
  }

  function createPanelWrap() {
    const wrap = document.createElement("div");
    wrap.id = WRAP_ID;
    Object.assign(wrap.style, {
      position: "fixed",
      zIndex: "2147483647",
      display: "none",
      alignItems: "center",
      justifyContent: "center",
      width: "36px",
      height: "36px",
      pointerEvents: "auto",
      margin: "0",
      padding: "0",
      border: "none",
      background: "transparent",
    });

    const btn = document.createElement("button");
    stylePanel(btn);
    btn.addEventListener("click", onClick, true);
    btn.addEventListener("mousedown", (ev) => ev.stopPropagation(), true);
    btn.addEventListener("pointerdown", (ev) => ev.stopPropagation(), true);
    wrap.appendChild(btn);
    document.documentElement.appendChild(wrap);
    panelWrap = wrap;
    panelBtn = btn;
    return wrap;
  }

  function findPanelAnchor() {
    const bar = findPlayerBar();
    if (!bar) {
      return null;
    }

    const download = bar.querySelector(
      "button[aria-label*='Скачать'], button[aria-label*='Download'], button[aria-label*='download']"
    );
    if (download) {
      const rect = download.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        return {
          left: rect.left - 42,
          top: rect.top + (rect.height - 36) / 2,
        };
      }
    }

    const host = findPanelActionsHost(bar);
    if (host) {
      const rect = host.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        return {
          left: rect.left - 6,
          top: rect.top + (rect.height - 36) / 2,
        };
      }
    }

    const barRect = bar.getBoundingClientRect();
    if (barRect.width > 0 && barRect.height > 0) {
      return {
        left: barRect.right - 210,
        top: barRect.top + (barRect.height - 36) / 2,
      };
    }
    return null;
  }

  function positionPanelWrap() {
    if (!panelWrap) {
      return false;
    }
    const anchor = findPanelAnchor();
    if (!anchor) {
      panelWrap.style.display = "none";
      return false;
    }
    panelWrap.style.display = "flex";
    panelWrap.style.left = `${Math.round(anchor.left)}px`;
    panelWrap.style.top = `${Math.round(anchor.top)}px`;
    return true;
  }

  function removeLegacyPanelNodes() {
    document.querySelectorAll(`#${PANEL_ID}`).forEach((node) => {
      if (!panelWrap || !panelWrap.contains(node)) {
        node.remove();
      }
    });
  }

  function styleInline(btn) {
    btn.id = PAGE_ID;
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
    btn.title = "Скачать текущий трек в TubeSave";
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
    const prev = btn.dataset.tsLabel || btn.textContent;
    const prevBg = btn.dataset.tsBg || btn.style.background;
    btn.dataset.tsLabel = prev;
    btn.dataset.tsBg = prevBg;
    btn.textContent = text;
    btn.style.background = bg;
    if (btn.id === PANEL_ID) {
      btn.style.width = text.length > 1 ? "auto" : "36px";
      btn.style.padding = text.length > 1 ? "0 10px" : "0";
      btn.style.borderRadius = text.length > 1 ? "18px" : "50%";
      btn.style.fontSize = text.length > 1 ? "11px" : "16px";
    }
    setTimeout(() => {
      btn.textContent = prev;
      btn.style.background = prevBg;
      if (btn.id === PANEL_ID) {
        btn.style.width = "36px";
        btn.style.padding = "0";
        btn.style.borderRadius = "50%";
        btn.style.fontSize = "16px";
      }
    }, 1400);
  }

  async function onClick(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    const btn = ev.currentTarget;
    btn.disabled = true;
    const url = await resolveTrackUrl();
    if (!url) {
      flash(btn, "!", "#B00020");
      btn.disabled = false;
      return;
    }
    if (window.TubeSaveLaunchProtocol) {
      window.TubeSaveLaunchProtocol(url, true, "best");
    }
    flash(btn, "…", "#C45C26");
    try {
      const response = await chrome.runtime.sendMessage({
        type: "tubesave-download",
        url,
        audio_only: true,
        protocol_fired: true,
      });
      if (response && response.ok) {
        flash(btn, "✓", "#1B7F4B");
      } else {
        flash(btn, "!", "#B00020");
        console.warn(response && response.error);
      }
    } catch (err) {
      flash(btn, "!", "#B00020");
      console.warn(err);
    } finally {
      btn.disabled = false;
    }
  }

  function findPlayerBar() {
    return (
      document.querySelector("[class*='PlayerBarDesktop']") ||
      document.querySelector("[class*='PlayerBar']") ||
      document.querySelector("[class*='player-bar']") ||
      document.querySelector("[data-testid='player-bar']") ||
      document.querySelector("[data-testid='player']") ||
      document.querySelector(".player-controls") ||
      null
    );
  }

  function findPanelActionsHost(bar) {
    if (!bar) {
      return null;
    }

    const byAria = Array.from(
      bar.querySelectorAll(
        "button[aria-label], a[aria-label], [role='button'][aria-label]"
      )
    ).filter((el) => {
      const label = (el.getAttribute("aria-label") || "").toLowerCase();
      return /текст|lyrics|очеред|queue|громк|volume|скач|download|настрой|setting|equaliz|эквал/i.test(
        label
      );
    });

    if (byAria.length) {
      const parent = byAria[0].parentElement;
      if (parent) {
        return parent;
      }
    }

    const candidates = Array.from(
      bar.querySelectorAll("div, section, ul, nav")
    ).filter((el) => {
      if (el.closest(`#${PANEL_ID}`)) {
        return false;
      }
      const buttons = el.querySelectorAll("button, [role='button'], a");
      if (buttons.length < 2 || buttons.length > 10) {
        return false;
      }
      const rect = el.getBoundingClientRect();
      if (rect.width < 80 || rect.height < 28 || rect.height > 90) {
        return false;
      }
      // Prefer right half of the bar.
      const barRect = bar.getBoundingClientRect();
      return rect.left > barRect.left + barRect.width * 0.45;
    });

    candidates.sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return br.left - ar.left || a.querySelectorAll("button").length - b.querySelectorAll("button").length;
    });

    return candidates[0] || null;
  }

  function findPageHost() {
    return (
      document.querySelector(".page-track__actions") ||
      document.querySelector("[class*='TrackPage'][class*='Actions'], [class*='TrackPage'] [class*='Actions']") ||
      document.querySelector(".sidebar-track__actions") ||
      document.querySelector(".entity-actions") ||
      document.querySelector(".d-track__actions") ||
      null
    );
  }

  function ensurePanelButton() {
    removeLegacyPanelNodes();
    if (!panelWrap || !panelWrap.isConnected) {
      createPanelWrap();
    }
    return positionPanelWrap();
  }

  function ensurePageButton() {
    const host = findPageHost();
    if (!host || document.getElementById(PAGE_ID)) {
      return;
    }
    const btn = document.createElement("button");
    styleInline(btn);
    btn.addEventListener("click", onClick);
    host.appendChild(btn);
  }

  function ensureFloatFallback(needFloat) {
    const existing = document.getElementById(FLOAT_ID);
    if (!needFloat) {
      existing?.remove();
      return;
    }
    if (existing) {
      return;
    }
    const fab = document.createElement("button");
    styleFloat(fab);
    fab.addEventListener("click", onClick);
    document.documentElement.appendChild(fab);
  }

  function ensureButtons() {
    const onPanel = ensurePanelButton();
    ensurePageButton();
    ensureFloatFallback(!onPanel);
  }

  ensureButtons();
  const observer = new MutationObserver(() => ensureButtons());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("scroll", () => positionPanelWrap(), true);
  window.addEventListener("resize", () => positionPanelWrap());
  setInterval(() => positionPanelWrap(), 500);

  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      document.getElementById(PAGE_ID)?.remove();
      ensureButtons();
    }
  }, 700);
})();
