// MediaGrab browser extension - background service worker
const APP = "http://127.0.0.1:9614";
const MEDIA_EXT = /\.(m3u8|mpd|mp4|webm|mkv|mov|flv|mp3|m4a|aac|ogg|opus|wav|flac)(\?|#|$)/i;
const SKIP = /\.(ts|m4s|vtt|srt)(\?|#|$)|googlevideo\.com\/videoplayback|fbcdn\.net\/.*bytestart|cdninstagram\.com\/.*bytestart/i;
const MEDIA_TYPES = /^(video\/|audio\/|application\/(vnd\.apple\.mpegurl|x-mpegurl|dash\+xml))/i;
const MIME_EXT = {
  "application/zip": "zip", "application/x-zip-compressed": "zip", "application/x-rar-compressed": "rar",
  "application/vnd.rar": "rar", "application/x-7z-compressed": "7z", "application/pdf": "pdf",
  "application/x-msdownload": "exe", "application/x-msdos-program": "exe", "application/x-msi": "msi",
  "application/vnd.android.package-archive": "apk", "application/x-iso9660-image": "iso",
  "video/mp4": "mp4", "video/x-matroska": "mkv", "audio/mpeg": "mp3", "application/gzip": "gz",
};

// ---------- talking to the app
let appCache = { t: 0, info: null };
async function ping(force) {
  if (!force && Date.now() - appCache.t < 5000) return appCache.info;
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 1500);
    const r = await fetch(APP + "/ping", { signal: ctl.signal });
    clearTimeout(timer);
    appCache = { t: Date.now(), info: r.ok ? await r.json() : null };
  } catch (e) {
    appCache = { t: Date.now(), info: null };
  }
  return appCache.info;
}

async function cookieHeader(url) {
  try {
    const list = await chrome.cookies.getAll({ url });
    return list.map((c) => c.name + "=" + c.value).join("; ");
  } catch (e) {
    return "";
  }
}

async function sendToApp(url, extra = {}) {
  try {
    const body = { url, user_agent: navigator.userAgent, ...extra };
    if (extra.withCookies) body.cookies = await cookieHeader(url);
    delete body.withCookies;
    const r = await fetch(APP + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.ok;
  } catch (e) {
    return false;
  }
}

function notifyFail(tabId) {
  chrome.action.setBadgeText({ tabId, text: "off" });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#c62828" });
}

// ---------- catch browser downloads (like IDM)
const letThrough = new Set();
chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url || "";
  if (!/^https?:/i.test(url)) return;                // blob:/data: downloads stay in the browser
  if (letThrough.has(url)) { letThrough.delete(url); return; }
  const { catchDownloads = true } = await chrome.storage.local.get("catchDownloads");
  if (!catchDownloads) return;
  const app = await ping();
  if (!app || app.catch_downloads === false) return;
  const fromName = (item.filename || "").split(/[\\/]/).pop();
  const fromUrl = decodeURIComponent(url.split(/[?#]/)[0].split("/").pop() || "");
  let ext = ((fromName || fromUrl).match(/\.([a-z0-9]{1,8})$/i) || [])[1];
  ext = (ext || MIME_EXT[(item.mime || "").split(";")[0]] || "").toLowerCase();
  const types = app.catch_types || [];
  if (!ext || !types.includes(ext)) return;
  const size = item.totalBytes > 0 ? item.totalBytes : item.fileSize > 0 ? item.fileSize : 0;
  if (size && size < (app.min_size_kb || 0) * 1024) return;

  try { await chrome.downloads.cancel(item.id); } catch (e) {}
  try { await chrome.downloads.erase({ id: item.id }); } catch (e) {}
  const ok = await sendToApp(url, {
    referer: item.referrer || "", filename: fromName || fromUrl, size,
    mode: "ask", engine: "file", withCookies: true,
  });
  if (!ok) { // app closed in the meantime: give the download back to the browser
    letThrough.add(url);
    chrome.downloads.download({ url });
  }
});

// ---------- right-click menu
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "mg-link", title: "Download with MediaGrab", contexts: ["link"] });
    chrome.contextMenus.create({ id: "mg-media", title: "Download this video/audio with MediaGrab", contexts: ["video", "audio"] });
    chrome.contextMenus.create({ id: "mg-page", title: "Download video on this page with MediaGrab", contexts: ["page", "frame"] });
    chrome.contextMenus.create({ id: "mg-mp3", title: "Download as MP3 with MediaGrab", contexts: ["page", "link", "video", "audio"] });
    chrome.contextMenus.create({ id: "mg-grab", title: "Grab all links on this page (MediaGrab grabber)", contexts: ["page", "selection"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "mg-grab") return openGrabber(tab.id);
  let url = info.pageUrl;
  if (info.menuItemId === "mg-link" || (info.menuItemId === "mg-mp3" && info.linkUrl)) url = info.linkUrl;
  if (info.menuItemId === "mg-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) url = info.srcUrl;
  const extra = { referer: info.pageUrl || "", mode: info.menuItemId === "mg-link" ? "ask" : "now", withCookies: info.menuItemId === "mg-link" };
  if (info.menuItemId === "mg-mp3") { extra.quality = "mp3"; extra.mode = "now"; }
  const ok = await sendToApp(url, extra);
  if (!ok && tab) notifyFail(tab.id);
});

function openGrabber(tabId) {
  chrome.tabs.create({ url: chrome.runtime.getURL("grabber.html?tab=" + tabId) });
}

// ---------- media sniffing
async function getMedia(tabId) {
  const key = "tab" + tabId;
  const data = await chrome.storage.session.get(key);
  return data[key] || [];
}

async function addMedia(tabId, item) {
  const key = "tab" + tabId;
  const list = await getMedia(tabId);
  if (list.some((m) => m.url === item.url)) return;
  list.push(item);
  if (list.length > 60) list.shift();
  await chrome.storage.session.set({ [key]: list });
  chrome.action.setBadgeText({ tabId, text: String(list.length) });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#1565c0" });
}

chrome.webRequest.onHeadersReceived.addListener(
  (d) => {
    if (d.tabId < 0 || SKIP.test(d.url) || d.url.startsWith(APP)) return;
    const h = (name) => (d.responseHeaders || []).find((x) => x.name.toLowerCase() === name);
    const type = (h("content-type") || {}).value || "";
    const size = parseInt((h("content-length") || {}).value || "0", 10);
    if (!(MEDIA_TYPES.test(type) || MEDIA_EXT.test(d.url))) return;
    const isPlaylist = /m3u8|mpd|mpegurl|dash\+xml/i.test(d.url + type);
    if (!isPlaylist && size && size < 300 * 1024) return;
    addMedia(d.tabId, { url: d.url, type: type.split(";")[0], size, isPlaylist, time: Date.now() });
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "object"] },
  ["responseHeaders"]
);

chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url) {
    chrome.storage.session.remove("tab" + tabId);
    chrome.action.setBadgeText({ tabId, text: "" });
  }
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove("tab" + tabId));

// ---------- messages from popup, grabber and page buttons
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "ping") {
      reply({ ok: !!(await ping(true)) });
    } else if (msg.type === "list") {
      reply({ media: await getMedia(msg.tabId) });
    } else if (msg.type === "send") {
      reply({ ok: await sendToApp(msg.url, msg.extra || {}) });
    } else if (msg.type === "grab") {
      openGrabber(msg.tabId);
      reply({ ok: true });
    }
  })();
  return true;
});
