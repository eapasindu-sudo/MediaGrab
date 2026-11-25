const $ = (id) => document.getElementById(id);
const send = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, res));

function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + " " + u[i];
}

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const saved = await chrome.storage.local.get("quality");
  if (saved.quality) $("q").value = saved.quality;
  $("q").onchange = () => chrome.storage.local.set({ quality: $("q").value });

  const ping = await send({ type: "ping" });
  $("conn").textContent = ping.ok ? "app connected" : "app not running";
  if (!ping.ok) $("warn").style.display = "block";

  const go = async (url, extra, btn) => {
    const r = await send({ type: "send", url, extra: { quality: $("q").value, ...extra } });
    if (r.ok) { btn.textContent = "Sent!"; setTimeout(() => window.close(), 700); }
    else { $("warn").style.display = "block"; }
  };

  $("main-btn").onclick = (e) => go(tab.url, { title: "", mode: "now" }, e.target);
  $("grab-btn").onclick = async () => { await send({ type: "grab", tabId: tab.id }); window.close(); };
  const c = await chrome.storage.local.get("catchDownloads");
  $("catch").checked = c.catchDownloads !== false;
  $("catch").onchange = () => chrome.storage.local.set({ catchDownloads: $("catch").checked });

  const { media } = await send({ type: "list", tabId: tab.id });
  if (media && media.length) {
    $("empty").style.display = "none";
    for (const m of media.slice().reverse()) {
      const li = document.createElement("li");
      const span = document.createElement("span");
      const name = decodeURIComponent(m.url.split("?")[0].split("/").pop() || m.url);
      span.textContent = (m.isPlaylist ? "[stream] " : "") + name + (m.size ? "  (" + fmtSize(m.size) + ")" : "");
      span.title = m.url;
      const b = document.createElement("button");
      b.className = "small";
      b.textContent = "Get";
      b.onclick = () => go(m.url, { referer: tab.url, title: tab.title, mode: "now", withCookies: true }, b);
      li.append(span, b);
      $("list").append(li);
    }
  }
})();
