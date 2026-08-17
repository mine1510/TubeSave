(() => {
  const params = new URLSearchParams(location.search);
  const url = params.get("url") || "";
  const auto = params.get("auto") || "1";
  const audio = params.get("audio") || "0";
  const quality = params.get("quality") || "best";
  if (!url) {
    return;
  }
  const href =
    "tubesave://download?url=" +
    encodeURIComponent(url) +
    "&auto=" +
    encodeURIComponent(auto) +
    "&audio=" +
    encodeURIComponent(audio) +
    "&quality=" +
    encodeURIComponent(quality);
  const a = document.createElement("a");
  a.href = href;
  a.rel = "noreferrer";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    location.replace(href);
  }, 50);
})();
