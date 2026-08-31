(() => {
  function protocolHref(url, audioOnly, quality, audioFormat) {
    let href =
      "tubesave://download?url=" +
      encodeURIComponent(url) +
      "&auto=1&audio=" +
      (audioOnly ? "1" : "0") +
      "&quality=" +
      encodeURIComponent(quality || "best");
    if (audioOnly && audioFormat) {
      href += "&audio_format=" + encodeURIComponent(audioFormat);
    }
    return href;
  }

  function isAppAliveSync() {
    try {
      const xhr = new XMLHttpRequest();
      xhr.open("GET", "http://127.0.0.1:17834/ping", false);
      xhr.send(null);
      if (xhr.status !== 200) {
        return false;
      }
      const data = JSON.parse(xhr.responseText || "{}");
      return Boolean(data && data.ok);
    } catch {
      return false;
    }
  }

  window.TubeSaveLaunchProtocol = function TubeSaveLaunchProtocol(url, audioOnly, quality, audioFormat) {
    if (!url || isAppAliveSync()) {
      return false;
    }
    const href = protocolHref(url, audioOnly, quality, audioFormat);
    const a = document.createElement("a");
    a.href = href;
    a.rel = "noreferrer";
    a.style.display = "none";
    (document.body || document.documentElement).appendChild(a);
    a.click();
    a.remove();
    return true;
  };
})();
