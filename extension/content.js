// MediaGrab: floating "Download / MP3" buttons on videos (YouTube, Facebook, Instagram and any site)
(() => {
  if (window.__mediagrab) return;
  window.__mediagrab = true;

  const host = document.createElement("div");
  host.style.cssText = "position:fixed;z-index:2147483647;top:0;left:0;display:none;pointer-events:auto;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      .bar { display:flex; gap:4px; font:600 12px/1 "Segoe UI",Roboto,Arial,sans-serif; opacity:.55; transition:opacity .15s; }
      .bar:hover { opacity:1; }
      button { all:unset; cursor:pointer; background:#1565c0; color:#fff; padding:6px 9px; border-radius:4px;
               box-shadow:0 1px 4px rgba(0,0,0,.4); white-space:nowrap; }
      button:hover { background:#0d47a1; }
      button.mp3 { background:#2e7d32; }
      button.mp3:hover { background:#1b5e20; }
      button.x { background:rgba(0,0,0,.55); padding:6px 7px; }
      .wrap { position:relative; }
      .menu { display:none; position:absolute; top:30px; left:0; background:#fff; border-radius:6px; min-width:170px;
              box-shadow:0 4px 16px rgba(0,0,0,.35); padding:4px 0; font:13px/1.2 "Segoe UI",Roboto,Arial,sans-serif; }
      .menu.open { display:block; }
      .menu div { padding:7px 14px; color:#222; cursor:pointer; white-space:nowrap; }
      .menu div:hover { background:#e3edfb; }
      .menu .hd { color:#777; font-size:11px; cursor:default; padding:4px 14px; }
      .menu .hd:hover { background:none; }
    </style>
    <div class="bar">
      <div class="wrap">
        <button class="vid" title="Download this video with MediaGrab - choose quality">&#11015; Download &#9662;</button>
        <div class="menu">
          <div class="hd">MediaGrab - choose quality</div>
          <div data-q="best">Best quality</div>
          <div data-q="2160">2160p (4K)</div>
          <div data-q="1440">1440p</div>
          <div data-q="1080">1080p (Full HD)</div>
          <div data-q="720">720p (HD)</div>
          <div data-q="480">480p</div>
          <div data-q="360">360p</div>
          <div class="hd">Audio only</div>
          <div data-q="mp3">MP3 audio</div>
          <div data-q="m4a">M4A audio</div>
        </div>
      </div>
      <button class="mp3" title="Convert to MP3 and download with MediaGrab">&#9835; MP3</button>
      <button class="x" title="Hide on this page">&#10005;</button>
    </div>`;
  const btnVid = root.querySelector(".vid");
  const btnMp3 = root.querySelector(".mp3");
  let hidden = false;
  let current = null;

  const SITE_PAGE = /(^|\.)(youtube\.com|youtube-nocookie\.com|facebook\.com|fb\.watch|instagram\.com|tiktok\.com|twitter\.com|x\.com|vimeo\.com|dailymotion\.com|twitch\.tv|reddit\.com|soundcloud\.com|bilibili\.com)$/i;

  function permalinkFor(video) {
    // In Facebook / Instagram feeds the page URL is the feed, so look for the post link near the video.
    let el = video;
    for (let i = 0; i < 25 && el; i++, el = el.parentElement) {
      const a = el.querySelector && el.querySelector(
        'a[href*="/reel/"], a[href*="/p/"], a[href*="/videos/"], a[href*="/watch/?v="], a[href*="/watch?v="], a[href*="/status/"], a[href*="/shorts/"]');
      if (a && a.href) return a.href;
      if (el.tagName === "ARTICLE" || el.getAttribute?.("role") === "article") break;
    }
    return null;
  }

  function urlFor(video) {
    const h = location.hostname;
    const isFeed = /(facebook|instagram)\.com$/i.test(h) &&
      !/\/(reel|reels|p|videos|watch|share|tv)\b/i.test(location.pathname);
    if (isFeed) return permalinkFor(video) || location.href;
    if (SITE_PAGE.test(h)) return location.href;
    const src = video.currentSrc || video.src || "";
    if (/^https?:/i.test(src)) return src;
    return location.href; // blob: stream -> let yt-dlp read the page
  }

  function pickVideo() {
    let best = null, bestArea = 0;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      const area = r.width * r.height;
      if (r.width < 200 || r.height < 110 || r.bottom < 0 || r.top > innerHeight) continue;
      const score = area * (v.paused ? 1 : 4);
      if (score > bestArea) { best = v; bestArea = score; }
    }
    return best;
  }

  function place() {
    if (hidden) return;
    current = pickVideo();
    const fs = document.fullscreenElement;
    const parent = fs || document.documentElement;
    if (host.parentNode !== parent) parent.appendChild(host);
    if (!current) { host.style.display = "none"; return; }
    const r = current.getBoundingClientRect();
    host.style.display = "block";
    host.style.top = Math.max(r.top + 8, 4) + "px";
    host.style.left = Math.max(r.right - (host.offsetWidth || 190) - 8, r.left + 8) + "px";
  }

  function send(quality, btn) {
    if (!current) return;
    const old = btn === btnVid ? "&#11015; Download &#9662;" : btn.innerHTML;
    const url = urlFor(current);
    chrome.storage.local.get("quality", (s) => {
      const q = quality || s.quality || "best";
      chrome.runtime.sendMessage(
        { type: "send", url, extra: { quality: q, referer: location.href, mode: "now", title: url === location.href ? "" : document.title } },
        (r) => {
          btn.textContent = r && r.ok ? "Sent ✓" : "Open MediaGrab app first";
          setTimeout(() => (btn.innerHTML = old), 2500);
        });
    });
  }

  const menu = root.querySelector(".menu");
  btnVid.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); menu.classList.toggle("open"); });
  menu.addEventListener("click", (e) => {
    e.stopPropagation(); e.preventDefault();
    const q = e.target.dataset && e.target.dataset.q;
    if (!q) return;
    menu.classList.remove("open");
    chrome.storage.local.set({ quality: q });
    send(q, btnVid);
  });
  document.addEventListener("click", () => menu.classList.remove("open"), true);
  btnMp3.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); send("mp3", btnMp3); });
  root.querySelector(".x").addEventListener("click", (e) => {
    e.stopPropagation(); hidden = true; host.style.display = "none";
  });

  setInterval(place, 400);
  addEventListener("scroll", place, { passive: true });
  addEventListener("resize", place);
  document.addEventListener("fullscreenchange", place);
  // YouTube is a single-page app: show the bar again after navigating to another video
  addEventListener("yt-navigate-finish", () => { hidden = false; place(); });
})();
