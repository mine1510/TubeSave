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

function yandexTrackId(url) {
  const text = String(url || "");
  const pathMatch = text.match(/\/track\/(\d+)/i);
  if (pathMatch) {
    return pathMatch[1];
  }
  const hashMatch = text.match(/#.*\/track\/(\d+)/i);
  if (hashMatch) {
    return hashMatch[1];
  }
  const queryMatch = text.match(/(?:[?&])(?:trackId|track_id|track)=(\d+)/i);
  return queryMatch ? queryMatch[1] : null;
}

async function yandexCookieHeader() {
  if (!chrome.cookies || !chrome.cookies.getAll) {
    return "";
  }
  try {
    const groups = await Promise.all([
      chrome.cookies.getAll({ domain: "yandex.ru" }),
      chrome.cookies.getAll({ domain: "yandex.net" }),
      chrome.cookies.getAll({ domain: "music.yandex.ru" }),
    ]);
    const wanted = new Set([
      "Session_id",
      "sessionid2",
      "yandexuid",
      "i",
      "yp",
      "ys",
      "L",
      "yashr",
    ]);
    const seen = new Set();
    const parts = [];
    for (const cookie of groups.flat()) {
      if (!cookie || !wanted.has(cookie.name) || seen.has(cookie.name)) {
        continue;
      }
      seen.add(cookie.name);
      parts.push(`${cookie.name}=${cookie.value}`);
    }
    return parts.join("; ");
  } catch {
    return "";
  }
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
  const cookies = isYandexMusic(url) ? await yandexCookieHeader() : "";
  const res = await fetch(`${BRIDGE}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      auto_start: autoStart,
      audio_only: audioOnly || isYandexMusic(url),
      quality: quality || "best",
      extension_id: chrome.runtime.id,
      cookies,
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
  const q = quality || "best";
  const page =
    chrome.runtime.getURL("launch.html") +
    "?" +
    new URLSearchParams({ url, auto, audio, quality: q }).toString();
  chrome.tabs.create({ url: page, active: false }, (tab) => {
    if (chrome.runtime.lastError) {
      console.warn(chrome.runtime.lastError.message);
      return;
    }
    const tabId = tab && tab.id;
    setTimeout(() => {
      if (tabId != null) {
        chrome.tabs.remove(tabId).catch(() => {});
      }
    }, 4000);
  });
}

function sendNative(url, autoStart, audioOnly, quality) {
  return new Promise((resolve, reject) => {
    if (!chrome.runtime.sendNativeMessage) {
      reject(new Error("nativeMessaging unavailable"));
      return;
    }
    chrome.runtime.sendNativeMessage(
      "com.tubesave.host",
      {
        url,
        auto: autoStart ? 1 : 0,
        audio: audioOnly ? 1 : 0,
        quality: quality || "best",
      },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(response || {});
      }
    );
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForBridge(timeoutMs = 25000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await pingBridge()) {
      return true;
    }
    await sleep(400);
  }
  return false;
}

async function downloadUrl(
  url,
  { notify = true, audioOnly = false, quality = "best", protocolFired = false } = {}
) {
  if (!url || !/^https?:\/\//i.test(url)) {
    throw new Error("Нет ссылки");
  }
  if (!isSupportedUrl(url)) {
    throw new Error("Сайт не поддерживается TubeSave");
  }
  if (isYandexMusic(url) && !yandexTrackId(url)) {
    throw new Error(
      "Нужна ссылка на трек (…/track/123). Откройте трек или запустите его в плеере."
    );
  }

  const audio = Boolean(audioOnly) || isYandexMusic(url);
  const q = quality || "best";
  if (await pingBridge()) {
    await sendToApp(url, true, audio, q);
    if (notify) {
      await setBadge("OK", "#2F6FED");
    }
    return { mode: "bridge", audio_only: audio, quality: q };
  }

  async function launchApp() {
    let mode = "protocol";
    let launched = false;
    try {
      const native = await sendNative(url, true, audio, q);
      if (native && native.ok) {
        mode = "native";
        launched = true;
      }
    } catch {
      // Host is registered only after TubeSave has been opened at least once.
    }
    if (!launched) {
      openViaProtocol(url, true, audio, q);
    }
    return mode;
  }

  let mode = protocolFired ? "protocol" : await launchApp();
  let ready = await waitForBridge(protocolFired ? 8000 : 25000);
  if (!ready && protocolFired) {
    mode = await launchApp();
    ready = await waitForBridge(20000);
  }
  if (!ready) {
    throw new Error(
      "Не удалось запустить TubeSave. Откройте приложение один раз, затем нажмите «Скачать» снова."
    );
  }
  await sendToApp(url, true, audio, q);
  if (notify) {
    await setBadge("OK", "#2F6FED");
  }
  return { mode, audio_only: audio, quality: q };
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
    protocolFired: Boolean(message.protocol_fired),
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
const GITHUB_RELEASES_LATEST =
  "https://api.github.com/repos/mine1510/TubeSave/releases/latest";
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

function newerManifest(a, b) {
  const extA = String((a && (a.extension || a.extension_version)) || "");
  const extB = String((b && (b.extension || b.extension_version)) || "");
  const appA = String((a && (a.app || a.app_version)) || "");
  const appB = String((b && (b.app || b.app_version)) || "");
  if (isNewer(extA, extB)) return a;
  if (isNewer(extB, extA)) return b;
  if (isNewer(appA, appB)) return a;
  return b;
}

async function fetchLatestReleaseManifest() {
  const res = await fetch(GITHUB_RELEASES_LATEST, { cache: "no-store" });
  if (!res.ok) {
    return null;
  }
  const data = await res.json();
  const tag = String(data.tag_name || "").replace(/^v/i, "");
  if (!tag) {
    return null;
  }
  const assets = Array.isArray(data.assets) ? data.assets : [];
  const ext = assets.find((item) => item && item.name === "TubeSave-Extension.zip");
  const app = assets.find((item) => item && item.name === "TubeSave-Windows.zip");
  return {
    app: tag,
    extension: tag,
    app_zip: app && app.browser_download_url,
    extension_zip: ext && ext.browser_download_url,
  };
}

async function fetchUpdateJson() {
  const candidates = [];
  for (const url of UPDATE_JSON_URLS) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) continue;
      const data = await res.json();
      if (data && (data.extension || data.extension_version || data.app)) {
        candidates.push(data);
      }
    } catch {
      // try next
    }
  }
  try {
    const release = await fetchLatestReleaseManifest();
    if (release) {
      candidates.push(release);
    }
  } catch {
    // ignore
  }
  if (!candidates.length) {
    return null;
  }
  return candidates.reduce(newerManifest);
}

function openUpdateLauncher() {
  const page = chrome.runtime.getURL("launch.html") + "?mode=update";
  chrome.windows.create(
    { url: page, type: "popup", width: 440, height: 180, focused: true },
    (win) => {
      if (chrome.runtime.lastError) {
        chrome.tabs.create({ url: page, active: true });
      }
      const windowId = win && win.id;
      setTimeout(() => {
        if (windowId != null) {
          chrome.windows.remove(windowId).catch(() => {});
        }
      }, 8000);
    }
  );
}

async function ensureAppForUpdate() {
  if (await pingBridge()) {
    return true;
  }
  try {
    const native = await sendNativeMessageUpdate();
    if (!(native && native.ok)) {
      openUpdateLauncher();
    }
  } catch {
    openUpdateLauncher();
  }
  return waitForBridge(25000);
}

function sendNativeMessageUpdate() {
  return new Promise((resolve, reject) => {
    if (!chrome.runtime.sendNativeMessage) {
      reject(new Error("nativeMessaging unavailable"));
      return;
    }
    chrome.runtime.sendNativeMessage("com.tubesave.host", { action: "update" }, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response || {});
    });
  });
}

async function applyUpdatesViaBridge(zipUrl) {
  const checkRes = await fetch(`${BRIDGE}/check-update`, { cache: "no-store" });
  if (checkRes.ok) {
    const check = await checkRes.json();
    if (check && (check.app_update || check.extension_update)) {
      const apply = await fetch(`${BRIDGE}/apply-updates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (apply.ok) {
        return apply.json();
      }
    }
  }
  return applyExtensionUpdateViaBridge(zipUrl);
}

async function checkAndUpdateExtension() {
  const remote = await fetchUpdateJson();
  if (!remote) {
    return { ok: false, reason: "no-manifest" };
  }
  const remoteExt = String(
    remote.extension || remote.extension_version || ""
  ).replace(/^v/i, "");
  const remoteApp = String(remote.app || remote.app_version || "").replace(/^v/i, "");
  const needExt = Boolean(remoteExt && isNewer(remoteExt, LOCAL_EXT_VERSION));
  const needApp = Boolean(remoteApp && isNewer(remoteApp, LOCAL_EXT_VERSION));
  if (!needExt && !needApp) {
    return { ok: true, update: false, local: LOCAL_EXT_VERSION, remote: remoteExt };
  }

  await setBadge("UP", "#C45C26");
  const zipUrl = remote.extension_zip || remote.extension_download_url || "";

  if (!(await pingBridge())) {
    const started = await ensureAppForUpdate();
    if (!started) {
      await setBadge("!", "#B00020");
      return { ok: false, reason: "app-not-started", local: LOCAL_EXT_VERSION, remote: remoteExt };
    }
  }

  try {
    await applyUpdatesViaBridge(zipUrl);
    await setBadge("OK", "#1B7F4B");
    setTimeout(() => {
      try {
        chrome.runtime.reload();
      } catch (err) {
        console.warn(err);
      }
    }, 2500);
    return {
      ok: true,
      update: true,
      applied: true,
      local: LOCAL_EXT_VERSION,
      remote: remoteExt,
    };
  } catch (err) {
    console.warn("update failed", err);
    await setBadge("!", "#B00020");
    return { ok: false, reason: "apply-failed", error: String(err && err.message ? err.message : err) };
  }
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
