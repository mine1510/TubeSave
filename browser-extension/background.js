const BRIDGE = "http://127.0.0.1:17834";

const SUPPORTED = [
  /(?:^|\.)youtube\.com$/i,
  /^youtu\.be$/i,
  /(?:^|\.)vk\.com$/i,
  /(?:^|\.)vkvideo\.ru$/i,
  /(?:^|\.)pornhub\.com$/i,
  /(?:^|\.)iwara\.tv$/i,
  /(?:^|\.)rule34\.xxx$/i,
  /(?:^|\.)rule34video\.com$/i,
  /^music\.yandex\.(?:ru|com|by|kz|ua)$/i,
  /^m\.music\.yandex\.(?:ru|com|by|kz|ua)$/i,
];

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

function isSupportedUrl(url) {
  const host = hostOf(url);
  return SUPPORTED.some((re) => re.test(host));
}

function isYandexMusic(url) {
  return /music\.yandex\./i.test(hostOf(url));
}

async function pingBridge() {
  try {
    const res = await fetch(`${BRIDGE}/ping`, { method: "GET", cache: "no-store" });
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data && data.ok);
  } catch {
    return false;
  }
}

async function sendToApp(url, autoStart = true, audioOnly = false, quality = "best") {
  const res = await fetch(`${BRIDGE}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      auto_start: autoStart,
      audio_only: audioOnly || isYandexMusic(url),
      quality: quality || "best",
    }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = await res.json();
  if (!data.ok) {
    throw new Error(data.error || "bridge error");
  }
  return data;
}

function openViaProtocol(url, autoStart = true, audioOnly = false, quality = "best") {
  const auto = autoStart ? "1" : "0";
  const audio = audioOnly || isYandexMusic(url) ? "1" : "0";
  const q = encodeURIComponent(quality || "best");
  const target =
    `tubesave://download?url=${encodeURIComponent(url)}&auto=${auto}&audio=${audio}&quality=${q}`;
  chrome.tabs.create({ url: target, active: false }, (tab) => {
    if (chrome.runtime.lastError) {
      console.warn(chrome.runtime.lastError.message);
      return;
    }
    if (tab && tab.id != null) {
      setTimeout(() => {
        chrome.tabs.remove(tab.id).catch(() => {});
      }, 1200);
    }
  });
}

async function downloadUrl(url, { notify = true, audioOnly = false, quality = "best" } = {}) {
  if (!url || !/^https?:\/\//i.test(url)) {
    throw new Error("Нет ссылки");
  }
  if (!isSupportedUrl(url)) {
    throw new Error("Сайт не поддерживается TubeSave");
  }

  const audio = Boolean(audioOnly) || isYandexMusic(url);
  const q = quality || "best";
  const alive = await pingBridge();
  if (alive) {
    await sendToApp(url, true, audio, q);
    if (notify) {
      await setBadge("OK", "#2F6FED");
    }
    return { mode: "bridge", audio_only: audio, quality: q };
  }

  openViaProtocol(url, true, audio, q);
  if (notify) {
    await setBadge("…", "#C45C26");
  }
  return { mode: "protocol", audio_only: audio, quality: q };
}

async function setBadge(text, color) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text });
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 1800);
  } catch {
    // ignore
  }
}

async function downloadActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    throw new Error("Нет активной вкладки");
  }
  return downloadUrl(tab.url, { audioOnly: isYandexMusic(tab.url) });
}

chrome.action.onClicked.addListener(async () => {
  try {
    await downloadActiveTab();
  } catch (err) {
    await setBadge("!", "#B00020");
    console.error(err);
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "tubesave-download") {
    return false;
  }
  downloadUrl(message.url || "", {
    audioOnly: Boolean(message.audio_only),
    quality: message.quality || "best",
  })
    .then((result) => sendResponse({ ok: true, ...result }))
    .catch((err) =>
      sendResponse({
        ok: false,
        error: String(err && err.message ? err.message : err),
      })
    );
  return true;
});

/* ===================== Auto-update ===================== */

const UPDATE_JSON_URLS = [
  "https://raw.githubusercontent.com/mine1510/TubeSave/master/update.json",
  "https://raw.githubusercontent.com/mine1510/TubeSave/cursor/quality-audio-theme-ui/update.json",
];
const RELEASES_PAGE = "https://github.com/mine1510/TubeSave/releases/latest";
const LOCAL_EXT_VERSION = chrome.runtime.getManifest().version;

function parseVersion(text) {
  return String(text || "")
    .replace(/^v/i, "")
    .split(".")
    .map((part) => parseInt(part.replace(/\D/g, ""), 10) || 0);
}

function isNewer(remote, local) {
  const a = parseVersion(remote);
  const b = parseVersion(local);
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    const x = a[i] || 0;
    const y = b[i] || 0;
    if (x > y) return true;
    if (x < y) return false;
  }
  return false;
}

async function fetchUpdateJson() {
  for (const url of UPDATE_JSON_URLS) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) continue;
      const data = await res.json();
      if (data && (data.extension || data.extension_version || data.app)) {
        return data;
      }
    } catch {
      // try next
    }
  }
  return null;
}

async function applyExtensionUpdateViaBridge(zipUrl) {
  const res = await fetch(`${BRIDGE}/update-extension`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: zipUrl || "" }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = await res.json();
  if (!data.ok) {
    throw new Error(data.error || "update failed");
  }
  return data;
}

async function checkAndUpdateExtension() {
  const remote = await fetchUpdateJson();
  if (!remote) {
    return { ok: false, reason: "no-manifest" };
  }
  const remoteExt = String(
    remote.extension || remote.extension_version || ""
  ).replace(/^v/i, "");
  if (!remoteExt || !isNewer(remoteExt, LOCAL_EXT_VERSION)) {
    return { ok: true, update: false, local: LOCAL_EXT_VERSION, remote: remoteExt };
  }

  await setBadge("UP", "#C45C26");
  const zipUrl = remote.extension_zip || remote.extension_download_url || "";

  if (await pingBridge()) {
    try {
      await applyExtensionUpdateViaBridge(zipUrl);
      await setBadge("OK", "#1B7F4B");
      setTimeout(() => {
        try {
          chrome.runtime.reload();
        } catch (err) {
          console.warn(err);
        }
      }, 900);
      return {
        ok: true,
        update: true,
        applied: true,
        local: LOCAL_EXT_VERSION,
        remote: remoteExt,
      };
    } catch (err) {
      console.warn("bridge update failed", err);
    }
  }

  // App not running / update failed — open release page for manual install.
  chrome.tabs.create({
    url: remote.release_page || RELEASES_PAGE,
    active: false,
  });
  return {
    ok: true,
    update: true,
    applied: false,
    local: LOCAL_EXT_VERSION,
    remote: remoteExt,
  };
}

function scheduleUpdateChecks() {
  chrome.alarms.create("tubesave-update", { periodInMinutes: 360 });
  // First check shortly after load.
  chrome.alarms.create("tubesave-update-soon", { delayInMinutes: 1 });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || !String(alarm.name || "").startsWith("tubesave-update")) {
    return;
  }
  checkAndUpdateExtension().catch((err) => console.warn(err));
});

chrome.runtime.onInstalled.addListener(() => {
  scheduleUpdateChecks();
  checkAndUpdateExtension().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  scheduleUpdateChecks();
  checkAndUpdateExtension().catch(() => {});
});

scheduleUpdateChecks();
