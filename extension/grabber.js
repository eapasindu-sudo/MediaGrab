// MediaGrab grabber: list every link / media file on a page and send the chosen ones to the app.
const $ = (id) => document.getElementById(id);
const send = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, res));
const TYPES = {
  Video: /\.(mp4|mkv|webm|avi|mov|flv|wmv|m4v|3gp|m3u8|mpd)$/i,
  Audio: /\.(mp3|m4a|aac|wav|flac|ogg|opus|wma)$/i,
  Archives: /\.(zip|rar|7z|tar|gz|tgz|bz2|xz|iso)$/i,
  Programs: /\.(exe|msi|apk|dmg|deb|rpm|appimage)$/i,
  Documents: /\.(pdf|docx?|xlsx?|pptx?|txt|epub|csv|odt)$/i,
  Images: /\.(jpe?g|png|gif|webp|bmp|svg|tiff?|heic)$/i,
};
const COLORS = { Video: "#d84315", Audio: "#2e7d32", Archives: "#f9a825", Programs: "#6a1b9a", Documents: "#1565c0", Images: "#00838f", "Video page": "#c62828", Page: "#90a4ae" };
const VIDEO_SITES = /(^|\.)(youtube\.com|youtu\.be|facebook\.com|fb\.watch|instagram\.com|tiktok\.com|twitter\.com|x\.com|vimeo\.com|dailymotion\.com|soundcloud\.com)$/i;
let items = [], filter = "All", pageUrl = "";

function typeOf(url) {
  let path = url;
  try { const u = new URL(url); path = u.pathname; if (VIDEO_SITES.test(u.hostname) && /watch|reel|\/p\/|shorts|video|status|\/\d{5,}/.test(u.pathname + u.search)) return "Video page"; } catch (e) {}
  for (const [t, re] of Object.entries(TYPES)) if (re.test(path)) return t;
  return "Page";
}
function nameOf(url, text) {
  try {
    const last = decodeURIComponent(new URL(url).pathname.split("/").pop());
    if (last && /\.[a-z0-9]{2,5}$/i.test(last)) return last;
  } catch (e) {}
  return (text || "").trim().slice(0, 120) || url;
}

function collect() {
  // runs inside the web page
  const out = [];
  const add = (url, text, kind) => { if (url && /^https?:/i.test(url)) out.push({ url, text: text || "", kind }); };
  document.querySelectorAll("a[href]").forEach((a) => add(a.href, a.textContent || a.title, "link"));
  document.querySelectorAll("video, audio, source, track").forEach((m) => add(m.currentSrc || m.src, document.title, "media"));
  document.querySelectorAll("img[src]").forEach((i) => { if (i.naturalWidth >= 120 || i.width >= 120) add(i.currentSrc || i.src, i.alt, "image"); });
  document.querySelectorAll("iframe[src]").forEach((f) => add(f.src, "embedded", "frame"));
  return { url: location.href, title: document.title, items: out };
}

function render() {
  const q = $("q").value.toLowerCase();
  const counts = {};
  items.forEach((it) => (counts[it.type] = (counts[it.type] || 0) + 1));
  const order = ["All", "Video page", "Video", "Audio", "Archives", "Programs", "Documents", "Images", "Page"];
  $("chips").innerHTML = "";
  for (const t of order) {
    const n = t === "All" ? items.length : counts[t] || 0;
    if (!n && t !== "All") continue;
    const b = document.createElement("button");
    b.className = "chip" + (filter === t ? " on" : "");
    b.innerHTML = (t === "Page" ? "Other links" : t) + "<b>" + n + "</b>";
    b.onclick = () => { filter = t; render(); };
    $("chips").append(b);
  }
  const tb = $("rows");
  tb.innerHTML = "";
  let shown = 0;
  for (const it of items) {
    it.visible = (filter === "All" || it.type === filter) && (!q || (it.name + it.url).toLowerCase().includes(q));
    if (!it.visible) continue;
    shown++;
    const tr = document.createElement("tr");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = it.checked;
    cb.onchange = () => { it.checked = cb.checked; update(); };
    const td0 = document.createElement("td"); td0.append(cb);
    const td1 = document.createElement("td"); td1.textContent = it.name; td1.title = it.name;
    const td2 = document.createElement("td"); td2.className = "t";
    const sp = document.createElement("span"); sp.textContent = it.type; sp.style.background = COLORS[it.type] || "#777"; td2.append(sp);
    const td3 = document.createElement("td"); td3.textContent = it.url; td3.title = it.url;
    tr.append(td0, td1, td2, td3);
    tr.onclick = (e) => { if (e.target !== cb) { cb.checked = !cb.checked; cb.onchange(); } };
    tb.append(tr);
  }
  $("msg").style.display = shown ? "none" : "block";
  $("msg").textContent = items.length ? "Nothing matches this filter." : "No links or media found on this page.";
  update();
}
function update() {
  const n = items.filter((i) => i.checked).length;
  $("go").disabled = !n;
  $("go").textContent = n ? `Download ${n} selected` : "Download selected";
  const vis = items.filter((i) => i.visible);
  $("head").checked = vis.length > 0 && vis.every((i) => i.checked);
}
$("q").oninput = render;
$("all").onclick = () => { items.forEach((i) => { if (i.visible) i.checked = true; }); render(); };
$("none").onclick = () => { items.forEach((i) => (i.checked = false)); render(); };
$("head").onchange = () => { items.forEach((i) => { if (i.visible) i.checked = $("head").checked; }); render(); };
$("go").onclick = async () => {
  const chosen = items.filter((i) => i.checked);
  $("go").disabled = true;
  let ok = 0;
  for (const it of chosen) {
    const media = it.type === "Video page";
    const r = await send({ type: "send", url: it.url, extra: {
      referer: pageUrl, mode: "now", silent: true, withCookies: !media,
      quality: media || it.type === "Video" ? $("quality").value : "",
      engine: media ? "media" : "auto" } });
    if (r && r.ok) { ok++; it.checked = false; }
  }
  $("status").style.color = ok ? "#2e7d32" : "#c62828";
  $("status").textContent = ok ? `${ok} sent to MediaGrab ✓` : "MediaGrab app is not running - open it and try again.";
  render();
};

(async () => {
  const tabId = parseInt(new URLSearchParams(location.search).get("tab"), 10);
  let results = [];
  try {
    results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: collect });
  } catch (e) {
    $("msg").textContent = "This page can't be read (browser pages and the Web Store are blocked).";
    return;
  }
  const seen = new Set();
  for (const fr of results) {
    const res = fr.result;
    if (!res) continue;
    if (fr.frameId === 0) { pageUrl = res.url; $("src").textContent = res.title + "  -  " + res.url; }
    for (const it of res.items) {
      const url = it.url.split("#")[0];
      if (seen.has(url)) continue;
      seen.add(url);
      const type = typeOf(url);
      items.push({ url, type, name: nameOf(url, it.text), checked: false });
    }
  }
  const { media } = await send({ type: "list", tabId });
  for (const m of media || []) {
    if (seen.has(m.url)) continue;
    seen.add(m.url);
    items.push({ url: m.url, type: m.isPlaylist ? "Video" : (/audio/.test(m.type) ? "Audio" : "Video"), name: nameOf(m.url) + (m.isPlaylist ? " (stream)" : ""), checked: false });
  }
  const rank = (t) => ["Video page", "Video", "Audio", "Archives", "Programs", "Documents", "Images", "Page"].indexOf(t);
  items.sort((a, b) => rank(a.type) - rank(b.type));
  // pre-select the downloadable files (not plain page links or images)
  items.forEach((i) => (i.checked = ["Video", "Audio", "Archives", "Programs", "Documents"].includes(i.type)));
  if (items.some((i) => i.type !== "Page" && i.type !== "Images")) filter = "All";
  render();
})();
