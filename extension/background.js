// MediaGrab browser extension - background service worker
const APP = "http://127.0.0.1:9614";
const MEDIA_EXT = /\.(m3u8|mpd|mp4|webm|mkv|mov|flv|mp3|m4a|aac|ogg|opus|wav|flac)(\?|#|$)/i;
const SKIP = /\.(ts|m4s|vtt|srt)(\?|#|$)|googlevideo\.com\/videoplayback|fbcdn\.net\/.*bytestart|cdninstagram\.com\/.*bytestart/i;
const MEDIA_TYPES = /^(video\/|audio\/|application\/(vnd\.apple\.mpegurl|x-mpegurl|dash\+xml))/i;

async function sendToApp(url, extra = {}) {
  try {
    const r = await fetch(APP + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, ...extra }),
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

// ---------- right-click menu
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "mg-link", title: "Download link with MediaGrab", contexts: ["link"] });
  chrome.contextMenus.create({ id: "mg-media", title: "Download this video/audio with MediaGrab", contexts: ["video", "audio"] });
  chrome.contextMenus.create({ id: "mg-page", title: "Download video on this page with MediaGrab", contexts: ["page", "frame"] });
  chrome.contextMenus.create({ id: "mg-mp3", title: "Download as MP3 with MediaGrab", contexts: ["page", "link", "video", "audio"] });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let url = info.pageUrl;
  if (info.menuItemId === "mg-link" || (info.menuItemId === "mg-mp3" && info.linkUrl)) url = info.linkUrl;
  if (info.menuItemId === "mg-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) url = info.srcUrl;
  // blob: URLs (YouTube, Facebook, Instagram players) cannot be downloaded directly; use the page link instead
  const extra = { referer: info.pageUrl || "", title: tab ? tab.title : "" };
  if (info.menuItemId === "mg-mp3") extra.quality = "mp3";
  if (url === info.pageUrl) extra.title = "";
  const ok = await sendToApp(url, extra);
  if (!ok && tab) notifyFail(tab.id);
});

// ---------- media sniffing (like IDM's "download this video" button)
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
  if (list.length > 50) list.shift();
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
    const isMedia = MEDIA_TYPES.test(type) || MEDIA_EXT.test(d.url);
    if (!isMedia) return;
    // ignore tiny clips (ads, UI sounds) unless they are playlists
    const isPlaylist = /m3u8|mpd|mpegurl|dash\+xml/i.test(d.url + type);
    if (!isPlaylist && size && size < 300 * 1024) return;
    addMedia(d.tabId, { url: d.url, type: type.split(";")[0], size, isPlaylist, time: Date.now() });
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "object"] },
  ["responseHeaders"]
);

// clear the list when a tab navigates to a new page
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url) {
    chrome.storage.session.remove("tab" + tabId);
    chrome.action.setBadgeText({ tabId, text: "" });
  }
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove("tab" + tabId));

// ---------- messages from the popup
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "ping") {
      try {
        const r = await fetch(APP + "/ping");
        reply({ ok: r.ok });
      } catch (e) {
        reply({ ok: false });
      }
    } else if (msg.type === "list") {
      reply({ media: await getMedia(msg.tabId) });
    } else if (msg.type === "send") {
      reply({ ok: await sendToApp(msg.url, msg.extra || {}) });
    }
  })();
  return true;
});
