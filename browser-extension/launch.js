(() => {
  const params = new URLSearchParams(location.search);
  const mode = params.get("mode") || "download";
  const href =
    mode === "update"
      ? "tubesave://update"
      : (() => {
          const url = params.get("url") || "";
          if (!url) {
            return "";
          }
          const auto = params.get("auto") || "1";
          const audio = params.get("audio") || "0";
          const quality = params.get("quality") || "best";
          const audioFormat = params.get("audio_format") || "";
          let href =
            "tubesave://download?url=" +
            encodeURIComponent(url) +
            "&auto=" +
            encodeURIComponent(auto) +
            "&audio=" +
            encodeURIComponent(audio) +
            "&quality=" +
            encodeURIComponent(quality);
          if (audioFormat) {
            href += "&audio_format=" + encodeURIComponent(audioFormat);
          }
          return href;
        })();
  if (!href) {
    return;
  }
  const title = document.querySelector("h1");
  if (title && mode === "update") {
    title.textContent = "Обновляем TubeSave…";
  }
  const a = document.createElement("a");
  a.href = href;
  a.rel = "noreferrer";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    location.replace(href);
  }, 50);
})();
