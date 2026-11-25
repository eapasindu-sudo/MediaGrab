#!/usr/bin/env python3
"""
MediaGrab - an IDM-style download manager for video, audio and files.

* Video & audio from YouTube, Facebook, Instagram, TikTok, X and 1000+ sites
  (via yt-dlp + ffmpeg), including MP3 conversion.
* Fast multi-connection (segmented) downloads for normal files - zip, exe,
  pdf, iso... - with pause / resume.
* Categories, download queue, progress window, browser extension link
  (127.0.0.1:9614) that catches browser downloads and grabs links/media.
* Updates itself and yt-dlp automatically.

For personal use only. Respect each site's terms and copyright law.
"""
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "MediaGrab"
VERSION = "1.2.0"
UPDATE_REPO = "eapasindu-sudo/MediaGrab"  # GitHub repo that publishes new versions
PORT = 9614
IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")

PRESETS = ["Best video", "2160p (4K)", "1440p", "1080p", "720p", "480p", "360p",
           "Audio: MP3", "Audio: M4A"]
PRESET_ALIASES = {
    "best": "Best video", "2160": "2160p (4K)", "4k": "2160p (4K)", "1440": "1440p",
    "1080": "1080p", "720": "720p", "480": "480p", "360": "360p",
    "mp3": "Audio: MP3", "m4a": "Audio: M4A", "audio": "Audio: MP3",
}
COOKIE_BROWSERS = ["(none)", "firefox", "chrome", "edge", "brave", "opera", "vivaldi"]

CATEGORIES = {
    "Video": "mp4 mkv webm avi mov flv wmv m4v 3gp mpg mpeg ts m3u8 mpd",
    "Music": "mp3 m4a aac wav flac ogg opus wma",
    "Compressed": "zip rar 7z tar gz tgz bz2 xz iso cab",
    "Documents": "pdf doc docx xls xlsx ppt pptx txt rtf epub csv odt ods",
    "Programs": "exe msi apk dmg deb rpm appimage msix",
    "Images": "jpg jpeg png gif webp bmp svg psd tif tiff heic raw cr2 nef arw",
}
CAT_EXT = {c: set(v.split()) for c, v in CATEGORIES.items()}
CAT_COLORS = {"Video": "#d84315", "Music": "#2e7d32", "Compressed": "#f9a825",
              "Documents": "#1565c0", "Programs": "#6a1b9a", "Images": "#00838f",
              "Other": "#757575", "All": "#455a64", "Unfinished": "#ef6c00",
              "Finished": "#1a7f37"}
CATCH_TYPES = sorted(CAT_EXT["Compressed"] | CAT_EXT["Programs"] | CAT_EXT["Documents"] - {"txt", "csv"}
                     | {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "mp3", "m4a", "flac", "wav",
                        "psd", "raw", "torrent", "bin", "img"})
MEDIA_SITES = re.compile(
    r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|facebook\.com|fb\.watch|instagram\.com|"
    r"tiktok\.com|twitter\.com|x\.com|vimeo\.com|dailymotion\.com|soundcloud\.com|twitch\.tv|"
    r"reddit\.com|v\.redd\.it|bilibili\.com|pinterest\.com|threads\.net|linkedin\.com|"
    r"bandcamp\.com|mixcloud\.com|streamable\.com|ok\.ru|vk\.com|rumble\.com)$", re.I)

ACTIVE = ("Starting", "Connecting", "Downloading", "Processing")
DONE = "Complete"
URL_RE = re.compile(r"^https?://\S+$", re.I)


# --------------------------------------------------------------------------- paths

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


DATA_DIR = data_dir()
BIN_DIR = os.path.join(DATA_DIR, "bin")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")


def resource(name):
    """Path of a file bundled next to the app (or inside the PyInstaller exe)."""
    for d in (getattr(sys, "_MEIPASS", ""), app_dir()):
        p = os.path.join(d, name) if d else ""
        if p and os.path.exists(p):
            return p
    return None


def find_ytdlp():
    for d in (app_dir(), BIN_DIR):
        p = os.path.join(d, "yt-dlp" + EXE)
        if os.path.isfile(p):
            return [p]
    p = shutil.which("yt-dlp")
    if p:
        return [p]
    if not getattr(sys, "frozen", False):
        try:
            import yt_dlp  # noqa: F401
            return [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            pass
    return None


def find_ffmpeg():
    """Return a path for --ffmpeg-location ('' = on PATH) or None if missing."""
    for d in (app_dir(), BIN_DIR):
        if os.path.isfile(os.path.join(d, "ffmpeg" + EXE)):
            return d
    if shutil.which("ffmpeg"):
        return ""
    return None


def download_file(url, dest, progress=None):
    tmp = dest + ".tmp"
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, dest)


def install_ytdlp(progress=None):
    os.makedirs(BIN_DIR, exist_ok=True)
    download_file(YTDLP_URL, os.path.join(BIN_DIR, "yt-dlp.exe"), progress)


def install_ffmpeg(progress=None):
    os.makedirs(BIN_DIR, exist_ok=True)
    zpath = os.path.join(BIN_DIR, "ffmpeg.zip")
    download_file(FFMPEG_URL, zpath, progress)
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            base = os.path.basename(name)
            if base in ("ffmpeg.exe", "ffprobe.exe"):
                with z.open(name) as src, open(os.path.join(BIN_DIR, base), "wb") as dst:
                    shutil.copyfileobj(src, dst)
    os.remove(zpath)


# --------------------------------------------------------------------------- helpers

def vtuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s))[:3])


def fmt_size(n):
    if not n:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_eta(s):
    if s is None or s < 0:
        return ""
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} hr {m} min"
    if m:
        return f"{m} min {sec} sec"
    return f"{sec} sec"


def fmt_date(ts):
    return time.strftime("%b %d %H:%M", time.localtime(ts)) if ts else ""


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_preset(p):
    if not p:
        return None
    if p in PRESETS:
        return p
    k = str(p).lower().strip()
    if k.endswith("p") and k[:-1].isdigit():
        k = k[:-1]
    return PRESET_ALIASES.get(k)


def ext_of(name):
    name = (name or "").split("?")[0].split("#")[0]
    return name.rsplit(".", 1)[-1].lower() if "." in os.path.basename(name) else ""


def category_of(name):
    e = ext_of(name)
    for c, exts in CAT_EXT.items():
        if e in exts:
            return c
    return "Other"


def safe_filename(name):
    name = urllib.parse.unquote(name or "").strip().replace("\x00", "")
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return name[:180] or "download"


def unique_path(path):
    if not os.path.exists(path) and not os.path.exists(path + ".mgpart"):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}") or os.path.exists(f"{base} ({i}){ext}.mgpart"):
        i += 1
    return f"{base} ({i}){ext}"


def filename_from_headers(headers, url):
    cd = headers.get("Content-Disposition", "") if headers else ""
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I)
    if m:
        return safe_filename(urllib.parse.unquote(m.group(1).strip('"')))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if m:
        return safe_filename(m.group(1))
    path = urllib.parse.urlparse(url).path
    return safe_filename(os.path.basename(path) or urllib.parse.urlparse(url).hostname or "download")


def format_args(preset, has_ffmpeg):
    """yt-dlp arguments that pick the format for a quality preset."""
    if preset.startswith("Audio"):
        if not has_ffmpeg:
            return ["-f", "ba[ext=m4a]/ba/b"]
        if "MP3" in preset:
            return ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
        return ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a"]
    m = re.match(r"(\d+)p", preset)
    h = int(m.group(1)) if m else None
    if has_ffmpeg:
        sort = f"res:{h},ext:mp4:m4a" if h else "res,ext:mp4:m4a"
        return ["-f", "bv*+ba/b", "-S", sort, "--merge-output-format", "mp4"]
    return ["-f", f"b[height<={h}]/b" if h else "b"]


def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# --------------------------------------------------------------------------- task

class Task:
    FIELDS = ("id", "url", "referer", "title", "preset", "playlist", "folder", "status",
              "progress", "size", "downloaded", "error", "filepath", "added", "last_try",
              "finished", "engine", "filename", "category", "cookies", "user_agent",
              "resumable", "description")

    def __init__(self, url, preset, folder, playlist=False, referer="", title=""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.referer = referer or ""
        self.title = title or ""
        self.preset = preset
        self.playlist = playlist
        self.folder = folder          # base download folder
        self.status = "Queued"
        self.progress = 0.0
        self.size = 0
        self.downloaded = 0
        self.speed = None
        self.eta = None
        self.part = ""
        self.error = ""
        self.filepath = ""
        self.added = time.time()
        self.last_try = 0
        self.finished = 0
        self.engine = "auto"          # auto | media (yt-dlp) | file (segmented)
        self.filename = ""
        self.category = ""
        self.cookies = ""
        self.user_agent = ""
        self.resumable = None
        self.description = ""
        self.segments = []            # [[start, pos, end], ...] for file downloads
        self.connections = 0
        self.job = None
        self.stop_reason = None

    @property
    def name(self):
        if self.filepath:
            return os.path.basename(self.filepath)
        return self.filename or self.title or self.url

    def to_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    @classmethod
    def from_dict(cls, d):
        t = cls(d["url"], d.get("preset", PRESETS[0]), d.get("folder", ""))
        for k in cls.FIELDS:
            if k in d:
                setattr(t, k, d[k])
        if t.status in ACTIVE:
            t.status = "Paused"
        if t.status == "Done":  # 1.0/1.1 queue files
            t.status = DONE
        return t

    def headers(self):
        h = {"User-Agent": self.user_agent or UA, "Accept": "*/*"}
        if self.referer:
            h["Referer"] = self.referer
        if self.cookies:
            h["Cookie"] = self.cookies
        return h


# --------------------------------------------------------------------------- engines

class MediaJob:
    """Runs yt-dlp for video/audio sites (YouTube, Facebook, Instagram...)."""

    def __init__(self, app, t):
        self.app, self.t, self.proc = app, t, None

    def stop(self):
        kill_tree(self.proc)

    def build_cmd(self):
        app, t = self.app, self.t
        has_ff = app.ffmpeg is not None
        progress_tpl = ("download:PROG|%(progress.status)s|%(progress.downloaded_bytes)s|"
                        "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
                        "%(progress.speed)s|%(progress.eta)s")
        if t.playlist:
            out = "%(playlist_title|Playlist).80B/%(playlist_index|0)03d - %(title).150B [%(id)s].%(ext)s"
        else:
            out = "%(title).150B [%(id)s].%(ext)s"
        cmd = list(app.ytdlp) + [
            "--newline", "--progress", "--color", "never", "--encoding", "utf-8",
            "--progress-template", progress_tpl,
            "--print", "before_dl:TITLE|%(playlist_index|)s|%(n_entries|)s|%(title)s",
            "--print", "after_move:FILE|%(filepath)s",
            "--no-simulate", "--no-mtime", "--windows-filenames",
            "--concurrent-fragments", str(max(1, min(16, app.setting("connections")))),
            "--retries", "10", "--fragment-retries", "10",
            "-P", app.save_dir(t), "-o", out,
        ]
        cmd += format_args(t.preset, has_ff)
        if has_ff and app.ffmpeg:
            cmd += ["--ffmpeg-location", app.ffmpeg]
        if not t.playlist:
            cmd.append("--no-playlist")
        if t.referer and t.referer != t.url:
            cmd += ["--referer", t.referer]
        limit = app.setting("speed_limit")
        if limit:
            cmd += ["-r", f"{limit}K"]
        browser = app.setting("cookies_browser")
        if browser and browser != "(none)":
            cmd += ["--cookies-from-browser", browser]
        elif app.setting("cookies_file"):
            cmd += ["--cookies", app.setting("cookies_file")]
        cmd += ["--", t.url]
        return cmd

    def run(self):
        t = self.t
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        kw = {"creationflags": NO_WINDOW} if IS_WIN else {"start_new_session": True}
        last_lines, file_index = [], 0
        t.resumable = True
        t.connections = 1
        os.makedirs(self.app.save_dir(t), exist_ok=True)
        self.proc = subprocess.Popen(self.build_cmd(), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     env=env, **kw)
        for line in self.proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("PROG|"):
                p = line.split("|")
                done, total, est, speed, eta = (num(x) for x in p[2:7])
                total = total or est
                if p[1] == "downloading" and t.status != "Downloading":
                    t.status = "Downloading"
                    file_index += 1
                    if file_index > 1 and not t.playlist:
                        t.part = f"(part {file_index})"
                if total:
                    t.size = total
                    t.downloaded = done or 0
                    t.progress = min(100.0, (done or 0) * 100 / total)
                    t.segments = [[0, int(done or 0), int(total)]]
                t.speed, t.eta = speed, eta
                if p[1] == "finished":
                    t.status = "Processing"
            elif line.startswith("TITLE|"):
                _, idx, n, title = line.split("|", 3)
                t.title = title + (f"  [{idx}/{n}]" if idx and n and t.playlist else "")
                file_index, t.part, t.status = 0, "", "Downloading"
            elif line.startswith("FILE|"):
                t.filepath = line[5:]
                t.status = "Processing"
            elif line.startswith(("[Merger]", "[ExtractAudio]", "[VideoConvertor]",
                                  "[FixupM3u8]", "[FixupM4a]", "[ffmpeg]")):
                t.status = "Processing"
                t.speed = t.eta = None
            elif "has already been downloaded" in line:
                t.progress = 100.0
            if line.startswith("ERROR"):
                t.error = line.replace("ERROR: ", "", 1)
            last_lines = (last_lines + [line])[-8:]
            self.app.events.put(("update", t.id))
        rc = self.proc.wait()
        if rc != 0 and not t.error:
            t.error = " | ".join(last_lines[-3:]) or f"yt-dlp exited with code {rc}"
        if t.filepath and os.path.exists(t.filepath):
            t.size = t.downloaded = os.path.getsize(t.filepath)
        return rc == 0


class FileJob:
    """IDM-style segmented downloader: several connections per file, dynamic
    segment splitting (a finished connection takes over half of the slowest
    part), pause/resume through a .mgpart file + .json state."""
    CHUNK = 64 * 1024
    MIN_SPLIT = 512 * 1024

    def __init__(self, app, t, info):
        self.app, self.t, self.info = app, t, info
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self.error = None
        self.bytes_now = 0
        self.workers = []

    def stop(self):
        self.stop_ev.set()

    def _open(self, start=None, end=None):
        h = self.t.headers()
        if start is not None:
            h["Range"] = f"bytes={start}-{end - 1}" if end else f"bytes={start}-"
        req = urllib.request.Request(self.info["url"], headers=h)
        return urllib.request.urlopen(req, timeout=30)

    def _throttle(self, started):
        limit = self.app.setting("speed_limit") * 1024
        if limit:
            want = self.bytes_now / limit
            have = time.time() - started
            if want > have:
                time.sleep(min(1.0, want - have))

    def _worker(self, seg, path, started):
        f = open(path, "r+b")
        try:
            while not self.stop_ev.is_set():
                with self.lock:
                    if seg[1] >= seg[2]:
                        seg = self._steal()
                        if seg is None:
                            return
                tries = 0
                while not self.stop_ev.is_set():
                    try:
                        with self._open(seg[1], seg[2]) as r:
                            if r.status != 206 and seg[1] > 0:
                                raise IOError("server stopped accepting ranges")
                            while not self.stop_ev.is_set():
                                with self.lock:
                                    want = seg[2] - seg[1]
                                if want <= 0:
                                    break
                                data = r.read(min(self.CHUNK, want))
                                if not data:
                                    raise IOError("connection closed early")
                                with self.lock:
                                    n = min(len(data), seg[2] - seg[1])
                                    off = seg[1]
                                    seg[1] += n
                                    self.bytes_now += n
                                f.seek(off)
                                f.write(data[:n])
                                self._throttle(started)
                        break
                    except Exception as e:
                        tries += 1
                        if tries > 6:
                            self.error = f"Connection failed: {e}"
                            self.stop_ev.set()
                            return
                        time.sleep(min(10, 2 * tries))
        finally:
            f.close()

    def _steal(self):
        """Called with lock held: split the biggest remaining part (IDM dynamic segmentation)."""
        segs = self.t.segments
        best = max(segs, key=lambda s: s[2] - s[1], default=None)
        if not best or best[2] - best[1] < self.MIN_SPLIT * 2:
            return None
        mid = best[1] + (best[2] - best[1]) // 2
        new = [mid, mid, best[2]]
        best[2] = mid
        segs.append(new)
        return new

    def _save_state(self, state_path):
        with self.lock:
            data = {"url": self.info["url"], "size": self.info["size"], "segments": self.t.segments}
        try:
            with open(state_path, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def run(self):
        t, info = self.t, self.info
        size, resumable = info["size"], info["resumable"]
        t.resumable = resumable
        folder = self.app.save_dir(t)
        os.makedirs(folder, exist_ok=True)
        if not t.filepath:
            t.filepath = unique_path(os.path.join(folder, t.filename or info["filename"]))
        dest = t.filepath
        part, state_path = dest + ".mgpart", dest + ".mgpart.json"
        t.size = size

        if not (resumable and size):
            return self._run_single(dest, part)

        segs = None
        if os.path.exists(part) and os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    st = json.load(f)
                if st.get("size") == size:
                    segs = [list(s) for s in st["segments"]]
            except (OSError, ValueError, KeyError):
                segs = None
        if segs is None:
            with open(part, "wb") as f:
                f.truncate(size)
            n = max(1, min(self.app.setting("connections"), size // self.MIN_SPLIT or 1))
            step = size // n
            segs = [[i * step, i * step, size if i == n - 1 else (i + 1) * step] for i in range(n)]
        t.segments = segs
        t.downloaded = sum(s[1] - s[0] for s in segs)
        t.status = "Downloading"
        started = time.time()
        self.bytes_now = 0
        conns = max(1, self.app.setting("connections"))
        active = [s for s in segs if s[1] < s[2]]
        while len(active) < conns:  # more connections than unfinished parts: split
            with self.lock:
                s = self._steal()
            if not s:
                break
            active.append(s)
        for s in active[:conns]:
            th = threading.Thread(target=self._worker, args=(s, part, started), daemon=True)
            th.start()
            self.workers.append(th)

        last_save, last_bytes, last_t, speed = 0, 0, time.time(), 0.0
        while any(w.is_alive() for w in self.workers):
            time.sleep(0.4)
            now = time.time()
            with self.lock:
                done = sum(s[1] - s[0] for s in t.segments)
                t.connections = sum(1 for s in t.segments if s[1] < s[2])
            inst = (self.bytes_now - last_bytes) / max(0.01, now - last_t)
            speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
            last_bytes, last_t = self.bytes_now, now
            t.downloaded = done
            t.progress = done * 100 / size
            t.speed = speed
            t.eta = (size - done) / speed if speed > 1 else None
            if now - last_save > 2:
                self._save_state(state_path)
                last_save = now
            self.app.events.put(("update", t.id))
        self._save_state(state_path)
        t.connections = 0
        done = sum(s[1] - s[0] for s in t.segments)
        if self.stop_ev.is_set() or done < size:
            if self.error:
                t.error = self.error
            elif not self.stop_ev.is_set():
                t.error = "Download stopped before finishing"
            return False
        os.replace(part, dest)
        try:
            os.remove(state_path)
        except OSError:
            pass
        t.downloaded = t.size = size
        return True

    def _run_single(self, dest, part):
        """Server can't resume: one connection, restarts from zero after a pause."""
        t = self.t
        t.segments = []
        t.connections = 1
        t.status = "Downloading"
        started = time.time()
        done = 0
        try:
            with self._open() as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0) or t.size
                t.size = total
                last_t, last_b, speed = time.time(), 0, 0.0
                while not self.stop_ev.is_set():
                    data = r.read(self.CHUNK)
                    if not data:
                        break
                    f.write(data)
                    done += len(data)
                    self.bytes_now = done
                    self._throttle(started)
                    now = time.time()
                    if now - last_t > 0.4:
                        inst = (done - last_b) / (now - last_t)
                        speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
                        last_t, last_b = now, done
                        t.speed = speed
                        t.downloaded = done
                        if total:
                            t.progress = done * 100 / total
                            t.segments = [[0, done, total]]
                            t.eta = (total - done) / speed if speed > 1 else None
                        self.app.events.put(("update", t.id))
        except Exception as e:
            t.error = str(e)
            return False
        if self.stop_ev.is_set() or (total and done < total):
            return False
        os.replace(part, dest)
        t.size = t.downloaded = done
        return True


def probe(t):
    """Ask the server about a link: is it a web page (-> yt-dlp) or a file?"""
    req = urllib.request.Request(t.url, headers=dict(t.headers(), Range="bytes=0-0"))
    with urllib.request.urlopen(req, timeout=25) as r:
        ctype = r.headers.get_content_type()
        cr = r.headers.get("Content-Range", "")
        if r.status == 206 and "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            size, resumable = int(cr.rsplit("/", 1)[1]), True
        else:
            size, resumable = int(r.headers.get("Content-Length") or 0), False
        final = r.geturl()
        name = filename_from_headers(r.headers, final)
    if t.filename:
        name = safe_filename(t.filename)
    if "." not in name:
        guess = {"application/pdf": "pdf", "application/zip": "zip", "video/mp4": "mp4",
                 "audio/mpeg": "mp3", "application/x-msdownload": "exe",
                 "application/vnd.android.package-archive": "apk"}.get(ctype)
        if guess:
            name += "." + guess
    return {"url": final, "type": ctype, "size": size, "resumable": resumable, "filename": name}


# --------------------------------------------------------------------------- browser link

class LinkHandler(BaseHTTPRequestHandler):
    """Local API used by the browser extension. Only accepts requests from
    browser extensions (or local programs), never from normal web pages."""
    events = None
    info = staticmethod(lambda: {})
    server_version = f"{APP_NAME}/{VERSION}"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _origin_ok(self):
        o = self.headers.get("Origin")
        return o is None or o.startswith(("chrome-extension://", "moz-extension://",
                                          "extension://"))

    def _reply(self, code, obj=None):
        body = json.dumps(obj or {}).encode()
        self.send_response(code)
        o = self.headers.get("Origin")
        if o and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not (self._host_ok() and self._origin_ok()):
            return self._reply(403, {"error": "forbidden"})
        self._reply(204)

    def do_GET(self):
        if not (self._host_ok() and self._origin_ok()):
            return self._reply(403, {"error": "forbidden"})
        if self.path == "/ping":
            info = dict(self.info())
            info.update({"app": APP_NAME, "version": VERSION, "presets": PRESETS})
            return self._reply(200, info)
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not (self._host_ok() and self._origin_ok()):
            return self._reply(403, {"error": "forbidden"})
        if self.path != "/add":
            return self._reply(404, {"error": "not found"})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._reply(415, {"error": "json only"})
        length = min(int(self.headers.get("Content-Length") or 0), 262144)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._reply(400, {"error": "bad json"})
        url = str(data.get("url", "")).strip()
        if not URL_RE.match(url):
            return self._reply(400, {"error": "bad url"})
        self.events.put(("add", {
            "url": url,
            "referer": str(data.get("referer", ""))[:2000],
            "title": str(data.get("title", ""))[:300],
            "quality": str(data.get("quality", ""))[:30],
            "filename": str(data.get("filename", ""))[:250],
            "cookies": str(data.get("cookies", ""))[:16000],
            "user_agent": str(data.get("user_agent", ""))[:400],
            "size": int(num(data.get("size")) or 0),
            "mode": str(data.get("mode", "now"))[:10],
            "silent": bool(data.get("silent")),
            "engine": str(data.get("engine", "auto"))[:10],
        }))
        self._reply(200, {"ok": True})


# --------------------------------------------------------------------------- icons

def make_icon(color, size=16, kind="square"):
    img = tk.PhotoImage(width=size, height=size)
    r = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    light = "#%02x%02x%02x" % tuple(min(255, c + 70) for c in r)
    for y in range(size):
        row = []
        for x in range(size):
            corner = (x in (0, size - 1)) and (y in (0, size - 1))
            if kind == "folder" and y < 3 and x > size // 2:
                row.append("")
            elif corner:
                row.append("")
            elif kind == "file" and x > size - 5 and y < 4:
                row.append(light)
            else:
                row.append(color)
        img.put("{" + " ".join(c or "#ffffff" for c in row) + "}", to=(0, y))
        for x, c in enumerate(row):
            if not c:
                img.transparency_set(x, y, True)
    return img


# --------------------------------------------------------------------------- dialogs

class AddDialog(tk.Toplevel):
    """IDM-style 'Download File Info' window."""

    def __init__(self, app, data=None):
        super().__init__(app.root)
        self.app = app
        data = data or {}
        self.data = data
        self.title(f"{APP_NAME} - Download File Info")
        self.resizable(True, False)
        self.transient(app.root)
        self.configure(padx=12, pady=12)
        url = data.get("url", "")
        guess_name = data.get("filename") or (filename_from_headers({}, url) if url else "")
        media = is_media_url(url, data.get("quality"))
        cat = "Video" if media else category_of(guess_name)
        if normalize_preset(data.get("quality") or "") and normalize_preset(data["quality"]).startswith("Audio"):
            cat = "Music"

        self.url_var = tk.StringVar(value=url)
        self.cat_var = tk.StringVar(value=cat)
        self.folder_var = tk.StringVar(value=app.setting("folder"))
        self.name_var = tk.StringVar(value="" if media else guess_name)
        self.preset_var = tk.StringVar(value=normalize_preset(data.get("quality")) or app.setting("preset"))
        self.desc_var = tk.StringVar(value=data.get("title", ""))

        g = ttk.Frame(self)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        rows = [("URL", ttk.Entry(g, textvariable=self.url_var, width=70))]
        rows.append(("Category", ttk.Combobox(g, textvariable=self.cat_var, state="readonly",
                                              values=list(CATEGORIES) + ["Other"])))
        ff = ttk.Frame(g)
        ttk.Entry(ff, textvariable=self.folder_var).pack(side="left", fill="x", expand=True)
        ttk.Button(ff, text="...", width=3, command=self.browse).pack(side="left", padx=(4, 0))
        rows.append(("Save to", ff))
        rows.append(("File name", ttk.Entry(g, textvariable=self.name_var)))
        rows.append(("Video quality", ttk.Combobox(g, textvariable=self.preset_var, state="readonly",
                                                   values=PRESETS)))
        rows.append(("Description", ttk.Entry(g, textvariable=self.desc_var)))
        for i, (label, w) in enumerate(rows):
            ttk.Label(g, text=label + ":").grid(row=i, column=0, sticky="w", pady=3, padx=(0, 8))
            w.grid(row=i, column=1, sticky="ew", pady=3)
        size = data.get("size")
        info = ("Size: " + fmt_size(size)) if size else ("Video / audio page - choose quality above"
                                                         if media else "")
        ttk.Label(self, text=info, foreground="#555").pack(anchor="w", pady=(6, 0))
        ttk.Label(self, text="Leave 'File name' empty for video pages - the video title is used.",
                  foreground="#777").pack(anchor="w")

        b = ttk.Frame(self)
        b.pack(fill="x", pady=(10, 0))
        ttk.Button(b, text="Start Download", command=lambda: self.ok(True)).pack(side="right")
        ttk.Button(b, text="Download Later", command=lambda: self.ok(False)).pack(side="right", padx=6)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda e: self.ok(True))
        self.bind("<Escape>", lambda e: self.destroy())
        app.place_center(self)
        self.lift()
        self.focus_force()

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.folder_var.get())
        if d:
            self.folder_var.set(d)

    def ok(self, start):
        urls = [u for u in re.split(r"\s+", self.url_var.get().strip()) if URL_RE.match(u)]
        if not urls:
            messagebox.showwarning(APP_NAME, "Please enter a link starting with http.", parent=self)
            return
        for u in urls:
            d = dict(self.data, url=u, quality=self.preset_var.get(), title=self.desc_var.get())
            t = self.app.add_task(d, folder=self.folder_var.get().strip(),
                                  filename=self.name_var.get().strip() if len(urls) == 1 else "",
                                  category=self.cat_var.get(), start=start,
                                  show_progress=start and len(urls) == 1)
            t.description = self.desc_var.get()
        self.destroy()


def is_media_url(url, quality=None):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    q = normalize_preset(quality or "")
    return bool(MEDIA_SITES.search(host)) or ext_of(url) in ("m3u8", "mpd") or bool(q and q.startswith("Audio"))


class ProgressWindow(tk.Toplevel):
    """Per-download window with segmented progress, like IDM."""

    def __init__(self, app, t):
        super().__init__(app.root)
        self.app, self.t = app, t
        self.resizable(False, False)
        self.configure(padx=12, pady=10)
        self.protocol("WM_DELETE_WINDOW", self.close)
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        st = ttk.Frame(nb, padding=10)
        nb.add(st, text="Download status")
        self.vars = {}
        for i, key in enumerate(("URL", "Status", "File size", "Downloaded", "Transfer rate",
                                 "Time left", "Resume capability", "Saved to")):
            ttk.Label(st, text=key, foreground="#555").grid(row=i, column=0, sticky="w", pady=1)
            v = tk.StringVar()
            ttk.Label(st, textvariable=v, width=62).grid(row=i, column=1, sticky="w", padx=(10, 0))
            self.vars[key] = v
        self.bar = ttk.Progressbar(st, maximum=100, length=520)
        self.bar.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Label(st, text="Start positions and download progress by connections",
                  foreground="#555").grid(row=10, column=0, columnspan=2, sticky="w", pady=(6, 2))
        self.canvas = tk.Canvas(st, height=18, width=520, bg="#e8eef7", highlightthickness=1,
                                highlightbackground="#9aa9bf")
        self.canvas.grid(row=11, column=0, columnspan=2, sticky="ew")
        self.conn = ttk.Treeview(st, columns=("n", "done", "info"), show="headings", height=5)
        for c, text, w in (("n", "N.", 40), ("done", "Downloaded", 120), ("info", "Info", 340)):
            self.conn.heading(c, text=text)
            self.conn.column(c, width=w, anchor="w")
        self.conn.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        lim = ttk.Frame(nb, padding=10)
        nb.add(lim, text="Speed limiter")
        ttk.Label(lim, text="Speed limit for all downloads (KB/s, 0 = no limit):").pack(anchor="w")
        self.limit_var = tk.StringVar(value=str(app.setting("speed_limit")))
        ttk.Entry(lim, textvariable=self.limit_var, width=10).pack(anchor="w", pady=4)
        ttk.Button(lim, text="Apply", command=self.apply_limit).pack(anchor="w")

        bf = ttk.Frame(self)
        bf.pack(fill="x", pady=(8, 0))
        self.btn_pause = ttk.Button(bf, text="Pause", command=self.toggle)
        self.btn_pause.pack(side="right")
        ttk.Button(bf, text="Cancel", command=self.cancel).pack(side="right", padx=6)
        self.btn_open = ttk.Button(bf, text="Open", command=lambda: app.open_path(t.filepath))
        self.btn_folder = ttk.Button(bf, text="Open folder", command=lambda: app.reveal(t))
        self.done_shown = False
        self.refresh()
        app.place_center(self)

    def apply_limit(self):
        self.app.settings["speed_limit"] = App.safe_int(self.limit_var.get(), 0)
        self.app.save_settings()

    def toggle(self):
        if self.t.status in ACTIVE or self.t.status == "Queued":
            self.app.pause_tasks([self.t])
        else:
            self.app.resume_tasks([self.t])

    def cancel(self):
        if self.t.status != DONE:
            self.app.pause_tasks([self.t])
        self.close()

    def close(self):
        self.app.progress_windows.pop(self.t.id, None)
        self.destroy()

    def refresh(self):
        t = self.t
        pct = f"{t.progress:.0f}%"
        self.title(f"{pct} {t.name}" if t.status != DONE else f"Download complete - {t.name}")
        self.vars["URL"].set(t.url[:90])
        self.vars["Status"].set(t.status if t.status != "Error" else "Error: " + t.error[:70])
        self.vars["File size"].set(fmt_size(t.size) or "unknown")
        self.vars["Downloaded"].set(f"{fmt_size(t.downloaded) or '0 B'} ({t.progress:.2f} %)")
        self.vars["Transfer rate"].set((fmt_size(t.speed) + "/sec") if t.speed and t.status in ACTIVE else "")
        self.vars["Time left"].set(fmt_eta(t.eta) if t.status in ACTIVE else "")
        self.vars["Resume capability"].set({True: "Yes", False: "No"}.get(t.resumable, "checking..."))
        self.vars["Saved to"].set(t.filepath or self.app.save_dir(t))
        self.bar["value"] = t.progress
        self.btn_pause.config(text="Pause" if t.status in ACTIVE or t.status == "Queued" else "Resume")
        # segment bar
        c = self.canvas
        c.delete("all")
        w = int(c.winfo_width() or 520)
        if w < 50:
            w = 520
        total = t.size or 0
        segs = list(t.segments)
        if total and segs:
            for s in segs:
                x0 = s[0] * w / total
                x1 = s[1] * w / total
                c.create_rectangle(x0, 2, max(x0 + 1, x1), 17, fill="#1565c0", outline="")
                c.create_line(x0, 0, x0, 18, fill="#0b2f63")
        elif t.status == DONE:
            c.create_rectangle(0, 2, w, 17, fill="#1565c0", outline="")
        # connections table
        rows = self.conn.get_children()
        want = [(i + 1, fmt_size(s[1] - s[0]),
                 "Receiving data..." if s[1] < s[2] and t.status == "Downloading" else
                 ("Finished" if s[1] >= s[2] else "")) for i, s in enumerate(segs)]
        if len(rows) != len(want):
            self.conn.delete(*rows)
            for r in want:
                self.conn.insert("", "end", values=r)
        else:
            for iid, r in zip(rows, want):
                self.conn.item(iid, values=r)
        if t.status == DONE and not self.done_shown:
            self.done_shown = True
            self.btn_pause.pack_forget()
            self.btn_folder.pack(side="right", padx=6)
            self.btn_open.pack(side="right")
            self.bell()
            if not self.app.setting("show_complete"):
                self.close()


class OptionsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        s = app.settings
        self.title(f"{APP_NAME} - Options")
        self.transient(app.root)
        self.resizable(False, False)
        self.configure(padx=10, pady=10)
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.v = {}

        def tab(name):
            f = ttk.Frame(nb, padding=12)
            nb.add(f, text=name)
            return f

        def check(parent, key, text):
            self.v[key] = tk.BooleanVar(value=s.get(key))
            ttk.Checkbutton(parent, text=text, variable=self.v[key]).pack(anchor="w", pady=2)

        def entry(parent, key, text, width=10):
            row = ttk.Frame(parent)
            row.pack(anchor="w", pady=3, fill="x")
            ttk.Label(row, text=text).pack(side="left")
            self.v[key] = tk.StringVar(value=str(s.get(key)))
            ttk.Entry(row, textvariable=self.v[key], width=width).pack(side="left", padx=6)
            return row

        g = tab("General")
        row = entry(g, "folder", "Download folder:", 46)
        ttk.Button(row, text="...", width=3, command=self.browse).pack(side="left")
        check(g, "sort_folders", "Save each category in its own folder (Video, Music, Programs...)")
        check(g, "show_progress", "Show progress window when a download starts")
        check(g, "show_complete", "Show 'Download complete' window")
        check(g, "clipboard", "Catch download links I copy")
        check(g, "auto_update", "Update MediaGrab and yt-dlp automatically")
        row = ttk.Frame(g)
        row.pack(anchor="w", pady=3)
        ttk.Label(row, text="Default video quality:").pack(side="left")
        self.v["preset"] = tk.StringVar(value=s.get("preset"))
        ttk.Combobox(row, textvariable=self.v["preset"], values=PRESETS, state="readonly",
                     width=14).pack(side="left", padx=6)

        c = tab("Connection")
        entry(c, "max_parallel", "Max downloads at the same time:", 5)
        entry(c, "connections", "Connections per download (1-16):", 5)
        entry(c, "speed_limit", "Speed limit KB/s (0 = no limit):", 8)

        b = tab("Browser")
        check(b, "catch_downloads", "Catch downloads from the browser (needs the MediaGrab extension)")
        ttk.Label(b, text="Catch these file types:").pack(anchor="w", pady=(6, 0))
        self.types = tk.Text(b, height=5, width=60, wrap="word")
        self.types.insert("1.0", " ".join(s.get("catch_types") or CATCH_TYPES))
        self.types.pack(anchor="w", pady=4)
        entry(b, "min_size_kb", "Don't catch files smaller than (KB):", 8)
        ttk.Button(b, text="How to install the browser extension...",
                   command=app.show_extension).pack(anchor="w", pady=6)

        lg = tab("Sites login")
        ttk.Label(lg, text="Private Facebook / Instagram videos need your login.\n"
                           "Choose the browser where you are logged in (Firefox works best):",
                  justify="left").pack(anchor="w")
        self.v["cookies_browser"] = tk.StringVar(value=s.get("cookies_browser"))
        ttk.Combobox(lg, textvariable=self.v["cookies_browser"], values=COOKIE_BROWSERS,
                     state="readonly", width=12).pack(anchor="w", pady=6)
        row = entry(lg, "cookies_file", "Or a cookies.txt file:", 40)
        ttk.Button(row, text="...", width=3, command=self.browse_cookies).pack(side="left")

        bf = ttk.Frame(self)
        bf.pack(fill="x", pady=(10, 0))
        ttk.Button(bf, text="OK", command=self.ok).pack(side="right")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        app.place_center(self)

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.v["folder"].get())
        if d:
            self.v["folder"].set(d)

    def browse_cookies(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Cookies", "*.txt"), ("All", "*.*")])
        if p:
            self.v["cookies_file"].set(p)

    def ok(self):
        s = self.app.settings
        for k, var in self.v.items():
            val = var.get()
            if k in ("max_parallel", "connections", "speed_limit", "min_size_kb"):
                val = App.safe_int(val, s.get(k, 0))
                if k == "connections":
                    val = max(1, min(16, val))
                if k == "max_parallel":
                    val = max(1, min(10, val))
            s[k] = val
        s["catch_types"] = sorted(set(x.strip(". ").lower() for x in
                                      self.types.get("1.0", "end").split() if x.strip(". ")))
        self.app.save_settings()
        self.destroy()


# --------------------------------------------------------------------------- app

class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.tasks = {}
        self.order = []
        self.progress_windows = {}
        self.ytdlp = find_ytdlp()
        self.ffmpeg = find_ffmpeg()
        self.tools_busy = False
        self.last_clip = None
        self.clip_url = ""
        self.dirty = False
        self.server = None
        self.view = "All"
        self.settings = self.load_settings()

        root.title(f"{APP_NAME} Download Manager")
        root.geometry("1180x660")
        root.minsize(820, 440)
        self.build_ui()
        self.load_queue()
        self.start_link_server()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(300, self.tick)
        root.after(800, self.check_tools)

    # ----------------------------------------------------------------- settings
    def load_settings(self):
        s = {
            "folder": os.path.join(os.path.expanduser("~"), "Downloads", APP_NAME),
            "preset": PRESETS[0], "max_parallel": 3, "connections": 8,
            "cookies_browser": "(none)", "cookies_file": "", "clipboard": True,
            "playlist": False, "speed_limit": 0, "auto_update": True, "last_ytdlp_update": 0,
            "sort_folders": True, "show_progress": True, "show_complete": True,
            "catch_downloads": True, "catch_types": CATCH_TYPES, "min_size_kb": 200,
        }
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s.update(json.load(f))
        except (OSError, ValueError):
            pass
        if s.get("preset") not in PRESETS:
            s["preset"] = PRESETS[0]
        return s

    def setting(self, key):
        v = self.settings.get(key)
        if key in ("connections", "max_parallel", "speed_limit", "min_size_kb"):
            return self.safe_int(v, 0)
        return v

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except OSError:
            pass

    @staticmethod
    def safe_int(v, default):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return default

    def link_info(self):
        return {"catch_downloads": bool(self.settings.get("catch_downloads")),
                "catch_types": self.settings.get("catch_types") or CATCH_TYPES,
                "min_size_kb": self.setting("min_size_kb")}

    def load_queue(self):
        try:
            with open(QUEUE_FILE, encoding="utf-8") as f:
                for d in json.load(f):
                    t = Task.from_dict(d)
                    self.tasks[t.id] = t
                    self.order.append(t.id)
        except (OSError, ValueError, KeyError):
            pass
        self.refresh_list()

    def save_queue(self):
        try:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump([self.tasks[i].to_dict() for i in self.order], f, indent=1)
        except OSError:
            pass

    def save_dir(self, t):
        base = t.folder or self.setting("folder")
        if self.settings.get("sort_folders") and t.category:
            return os.path.join(base, t.category)
        return base

    # ----------------------------------------------------------------- UI
    def place_center(self, win):
        win.update_idletasks()
        r = self.root
        x = r.winfo_rootx() + (r.winfo_width() - win.winfo_width()) // 2
        y = r.winfo_rooty() + (r.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def build_ui(self):
        r = self.root
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=24)
        style.configure("Side.Treeview", rowheight=26)
        self.icons = {c: make_icon(CAT_COLORS[c], kind="file") for c in list(CATEGORIES) + ["Other"]}
        self.side_icons = {c: make_icon(col, kind="folder") for c, col in CAT_COLORS.items()}

        # menu
        mb = tk.Menu(r)
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Add URL...", accelerator="Ctrl+N", command=self.add_dialog)
        m.add_command(label="Add many links...", command=self.add_many_dialog)
        m.add_separator()
        m.add_command(label="Exit", command=self.on_close)
        mb.add_cascade(label="Tasks", menu=m)
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Resume", command=lambda: self.resume_tasks(self.selected()))
        m.add_command(label="Stop", command=lambda: self.pause_tasks(self.selected()))
        m.add_command(label="Stop all", command=self.stop_all)
        m.add_command(label="Resume all", command=self.resume_all)
        m.add_separator()
        m.add_command(label="Delete", command=self.remove)
        m.add_command(label="Delete completed", command=self.clear_finished)
        mb.add_cascade(label="Downloads", menu=m)
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Options...", command=lambda: OptionsDialog(self))
        m.add_command(label="Update yt-dlp now", command=self.update_ytdlp)
        m.add_command(label="Download / repair ffmpeg", command=lambda: self.install_tools(force_ffmpeg=True))
        m.add_command(label="Open download folder", command=lambda: self.open_path(self.ensure_dir(self.setting("folder"))))
        mb.add_cascade(label="Tools", menu=m)
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Install browser extension...", command=self.show_extension)
        m.add_command(label="Check for updates", command=lambda: self.check_app_update(manual=True))
        m.add_command(label="Supported sites", command=lambda: webbrowser.open(
            "https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"))
        m.add_command(label="About", command=self.about)
        mb.add_cascade(label="Help", menu=m)
        r.config(menu=mb)
        r.bind("<Control-n>", lambda e: self.add_dialog())

        # toolbar
        tb = tk.Frame(r, bg="#f3f6fb", bd=0, highlightthickness=0)
        tb.pack(fill="x")
        tk.Frame(r, height=1, bg="#c8d3e3").pack(fill="x")
        buttons = [
            ("✚", "Add URL", self.add_dialog, "#1565c0"),
            ("▶", "Resume", lambda: self.resume_tasks(self.selected()), "#2e7d32"),
            ("■", "Stop", lambda: self.pause_tasks(self.selected()), "#c62828"),
            ("■■", "Stop All", self.stop_all, "#8e0000"),
            ("✖", "Delete", self.remove, "#5d4037"),
            ("✔", "Delete Completed", self.clear_finished, "#1a7f37"),
            ("⚙", "Options", lambda: OptionsDialog(self), "#455a64"),
            ("↻", "Update", self.update_ytdlp, "#6a1b9a"),
            ("⬇", "Grabber", self.show_extension, "#00838f"),
        ]
        for glyph, text, cmd, color in buttons:
            f = tk.Frame(tb, bg="#f3f6fb", padx=4, pady=4, cursor="hand2")
            f.pack(side="left")
            g = tk.Label(f, text=glyph, font=("Segoe UI Symbol", 17), fg=color, bg="#f3f6fb")
            g.pack()
            t = tk.Label(f, text=text, font=("Segoe UI", 9), bg="#f3f6fb", fg="#222")
            t.pack()
            for w in (f, g, t):
                w.bind("<Button-1>", lambda e, c=cmd: c())
                w.bind("<Enter>", lambda e, fr=f: [x.config(bg="#dde7f5") for x in [fr] + fr.winfo_children()])
                w.bind("<Leave>", lambda e, fr=f: [x.config(bg="#f3f6fb") for x in [fr] + fr.winfo_children()])
        tk.Label(tb, text=APP_NAME, font=("Segoe UI", 15, "bold"), fg="#1565c0",
                 bg="#f3f6fb").pack(side="right", padx=14)

        body = ttk.PanedWindow(r, orient="horizontal")
        body.pack(fill="both", expand=True)

        # categories sidebar
        side = ttk.Frame(body)
        ttk.Label(side, text="Categories", padding=(8, 6, 0, 2), font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.side = ttk.Treeview(side, show="tree", style="Side.Treeview", selectmode="browse")
        self.side.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 4))
        self.side.insert("", "end", iid="All", text=" All Downloads", image=self.side_icons["All"], open=True)
        for c in list(CATEGORIES) + ["Other"]:
            self.side.insert("All", "end", iid=c, text=" " + c, image=self.side_icons[c])
        self.side.insert("", "end", iid="Unfinished", text=" Unfinished", image=self.side_icons["Unfinished"])
        self.side.insert("", "end", iid="Finished", text=" Finished", image=self.side_icons["Finished"])
        self.side.selection_set("All")
        self.side.bind("<<TreeviewSelect>>", self.on_side)
        body.add(side, weight=0)

        # download list
        mid = ttk.Frame(body)
        cols = ("size", "status", "eta", "rate", "date", "desc")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="File Name", anchor="w")
        self.tree.column("#0", width=320, stretch=True)
        for c, text, w, anchor in (
            ("size", "Size", 85, "e"), ("status", "Status", 110, "w"),
            ("eta", "Time left", 95, "w"), ("rate", "Transfer rate", 100, "e"),
            ("date", "Last Try Date", 105, "w"), ("desc", "Description", 110, "w"),
        ):
            self.tree.heading(c, text=text, anchor=anchor if anchor != "e" else "e")
            self.tree.column(c, width=w, anchor=anchor, stretch=False)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        body.add(mid, weight=1)
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Return>", self.on_double)
        self.tree.bind("<Button-3>", self.popup_menu)
        self.tree.bind("<Delete>", lambda e: self.remove())
        for st, color in (("Complete", "#1a7f37"), ("Error", "#c62828"), ("Paused", "#8a6d00")):
            self.tree.tag_configure(st, foreground=color)

        self.menu = tk.Menu(r, tearoff=0)
        self.menu.add_command(label="Open", command=self.open_file)
        self.menu.add_command(label="Open folder", command=lambda: [self.reveal(t) for t in self.selected()[:1]])
        self.menu.add_command(label="Show progress / properties", command=self.show_progress_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Resume", command=lambda: self.resume_tasks(self.selected()))
        self.menu.add_command(label="Stop", command=lambda: self.pause_tasks(self.selected()))
        self.menu.add_command(label="Redownload", command=self.redownload)
        self.menu.add_command(label="Delete", command=self.remove)
        self.menu.add_separator()
        self.menu.add_command(label="Copy address", command=self.copy_link)
        self.menu.add_command(label="Show error", command=self.show_error)

        # status bar
        sbar = ttk.Frame(r, padding=(6, 2))
        sbar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(sbar, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.clip_btn = ttk.Button(sbar, text="Download copied link", command=self.download_clip)
        self.speed_var = tk.StringVar()
        ttk.Label(sbar, textvariable=self.speed_var, anchor="e").pack(side="right")

    def set_status(self, text):
        self.status_var.set(text)

    def ensure_dir(self, d):
        os.makedirs(d, exist_ok=True)
        return d

    # ----------------------------------------------------------------- rows
    def task_category(self, t):
        if t.category:
            return t.category
        if is_media_url(t.url, t.preset):
            return "Music" if t.preset.startswith("Audio") else "Video"
        return category_of(t.filename or t.url)

    def visible(self, t):
        v = self.view
        if v == "All":
            return True
        if v == "Unfinished":
            return t.status != DONE
        if v == "Finished":
            return t.status == DONE
        return self.task_category(t) == v

    def row_values(self, t):
        active = t.status in ACTIVE
        if t.status == DONE:
            status = DONE
        elif t.status == "Downloading" and t.size:
            status = f"{t.progress:.2f}%" + (f" {t.part}" if t.part else "")
        elif t.status == "Error":
            status = "Error"
        elif t.status == "Paused" and t.progress:
            status = f"Paused {t.progress:.1f}%"
        else:
            status = t.status
        rate = (fmt_size(t.speed) + "/sec") if active and t.speed else ""
        eta = fmt_eta(t.eta) if active else ""
        desc = t.description or (t.preset if is_media_url(t.url, t.preset) else "")
        return (fmt_size(t.size), status, eta, rate, fmt_date(t.last_try or t.added), desc)

    def refresh_list(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for i in self.order:
            t = self.tasks[i]
            if self.visible(t):
                self.tree.insert("", "end", iid=t.id, text=" " + t.name,
                                 image=self.icons.get(self.task_category(t), self.icons["Other"]),
                                 values=self.row_values(t), tags=(t.status,))
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def refresh_row(self, t):
        if self.tree.exists(t.id):
            if not self.visible(t):
                self.tree.delete(t.id)
                return
            self.tree.item(t.id, text=" " + t.name, values=self.row_values(t), tags=(t.status,),
                           image=self.icons.get(self.task_category(t), self.icons["Other"]))
        elif self.visible(t):
            self.refresh_list()

    def on_side(self, _e=None):
        sel = self.side.selection()
        self.view = sel[0] if sel else "All"
        self.refresh_list()

    def selected(self):
        return [self.tasks[i] for i in self.tree.selection() if i in self.tasks]

    def on_double(self, _e=None):
        sel = self.selected()
        if not sel:
            return
        t = sel[0]
        if t.status == DONE:
            if not self.open_path(t.filepath):
                self.reveal(t)
        else:
            self.show_progress(t)

    def show_progress(self, t):
        w = self.progress_windows.get(t.id)
        if w and w.winfo_exists():
            w.lift()
            return
        self.progress_windows[t.id] = ProgressWindow(self, t)

    def show_progress_selected(self):
        for t in self.selected()[:3]:
            self.show_progress(t)

    # ----------------------------------------------------------------- adding
    def add_dialog(self, data=None):
        if data is None:
            data = {}
            try:
                clip = self.root.clipboard_get().strip()
                if URL_RE.match(clip):
                    data["url"] = clip
            except tk.TclError:
                pass
        AddDialog(self, data)

    def add_many_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Add many links")
        win.geometry("600x340")
        ttk.Label(win, text="Paste links, one per line:", padding=8).pack(anchor="w")
        txt = tk.Text(win, height=12)
        txt.pack(fill="both", expand=True, padx=8)

        def ok():
            for u in re.split(r"\s+", txt.get("1.0", "end")):
                if URL_RE.match(u):
                    self.add_task({"url": u}, show_progress=False)
            win.destroy()
        ttk.Button(win, text="Add all to queue", command=ok).pack(pady=8)
        self.place_center(win)

    def add_task(self, data, folder=None, filename=None, category=None, start=True,
                 show_progress=None):
        preset = normalize_preset(data.get("quality")) or self.setting("preset")
        t = Task(data["url"], preset, folder or self.setting("folder"),
                 playlist=bool(data.get("playlist")), referer=data.get("referer", ""),
                 title=data.get("title", ""))
        t.filename = safe_filename(filename) if filename else safe_filename(data["filename"]) if data.get("filename") else ""
        t.cookies = data.get("cookies", "")
        t.user_agent = data.get("user_agent", "")
        t.size = data.get("size") or 0
        if data.get("engine") in ("media", "file"):
            t.engine = data["engine"]
        t.category = category or ""
        if not t.category:
            t.category = self.task_category(t) if (t.filename or is_media_url(t.url, t.preset)) else ""
        if not start:
            t.status = "Paused"
        self.tasks[t.id] = t
        self.order.append(t.id)
        self.refresh_row(t)
        if self.tree.exists(t.id):
            self.tree.see(t.id)
        self.save_queue()
        self.set_status(f"Added: {t.name}")
        if show_progress is None:
            show_progress = not data.get("silent")
        if start and show_progress and self.settings.get("show_progress"):
            self.show_progress(t)
        return t

    def handle_external_add(self, data):
        if data.get("mode") == "ask":
            self.bring_to_front()
            self.add_dialog(data)
        else:
            self.add_task(data)
            if not data.get("silent"):
                self.bring_to_front()

    # ----------------------------------------------------------------- actions
    def pause_tasks(self, tasks):
        for t in tasks:
            if t.status in ACTIVE:
                t.stop_reason = "pause"
                if t.job:
                    t.job.stop()
            elif t.status == "Queued":
                t.status = "Paused"
                self.refresh_row(t)
        self.save_queue()

    def resume_tasks(self, tasks):
        for t in tasks:
            if t.status in ("Paused", "Error", "Cancelled"):
                t.status, t.error, t.stop_reason = "Queued", "", None
                self.refresh_row(t)
        self.save_queue()

    def stop_all(self):
        self.pause_tasks([self.tasks[i] for i in self.order])

    def resume_all(self):
        self.resume_tasks([self.tasks[i] for i in self.order])

    def redownload(self):
        for t in self.selected():
            if t.status in ACTIVE:
                continue
            for p in (t.filepath, t.filepath + ".mgpart", t.filepath + ".mgpart.json"):
                if t.status != DONE and p and os.path.exists(p) and p != t.filepath:
                    os.remove(p)
            t.filepath, t.progress, t.downloaded, t.segments = "", 0.0, 0, []
            t.status, t.error = "Queued", ""
            self.refresh_row(t)
        self.save_queue()

    def remove(self):
        sel = self.selected()
        if not sel:
            return
        running = any(t.status in ACTIVE for t in sel)
        if not messagebox.askyesno(APP_NAME, f"Delete {len(sel)} download(s) from the list?"
                                   + ("\n\nRunning downloads will be stopped." if running else "")
                                   + "\n\n(Finished files stay on your disk.)"):
            return
        for t in sel:
            if t.status in ACTIVE:
                t.stop_reason = "cancel"
                if t.job:
                    t.job.stop()
            elif t.status != DONE and t.filepath:
                for p in (t.filepath + ".mgpart", t.filepath + ".mgpart.json"):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            if self.tree.exists(t.id):
                self.tree.delete(t.id)
            self.order.remove(t.id)
            del self.tasks[t.id]
            w = self.progress_windows.pop(t.id, None)
            if w:
                w.destroy()
        self.save_queue()

    def clear_finished(self):
        for i in list(self.order):
            if self.tasks[i].status == DONE:
                if self.tree.exists(i):
                    self.tree.delete(i)
                self.order.remove(i)
                del self.tasks[i]
        self.save_queue()

    def copy_link(self):
        sel = self.selected()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(sel[0].url)
            self.last_clip = sel[0].url

    def show_error(self):
        sel = self.selected()
        if sel:
            messagebox.showinfo(APP_NAME, sel[0].error or "No error for this download.")

    def open_path(self, path):
        if not path or not os.path.exists(path):
            return False
        if IS_WIN:
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True

    def open_file(self):
        sel = self.selected()
        if sel and not self.open_path(sel[0].filepath):
            self.reveal(sel[0])

    def reveal(self, t):
        if t.filepath and os.path.exists(t.filepath) and IS_WIN:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(t.filepath)])
            return
        folder = os.path.dirname(t.filepath) if t.filepath else self.save_dir(t)
        self.open_path(self.ensure_dir(folder))

    def popup_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    def about(self):
        messagebox.showinfo(APP_NAME, f"{APP_NAME} {VERSION}\n\n"
                            "Download manager for video, audio and files.\n"
                            "Powered by yt-dlp and ffmpeg.\n\n"
                            "For personal use only. Please respect each website's terms of "
                            "service and copyright law.")

    # ----------------------------------------------------------------- tools (yt-dlp, ffmpeg)
    def check_tools(self):
        if self.ytdlp and self.ffmpeg is not None:
            self.startup_updates()
            return
        if not IS_WIN:
            missing = []
            if not self.ytdlp:
                missing.append("yt-dlp  (pip install -U yt-dlp)")
            if self.ffmpeg is None:
                missing.append("ffmpeg  (install it with your package manager)")
            messagebox.showwarning(APP_NAME, "Please install:\n\n" + "\n".join(missing))
            return
        need = []
        if not self.ytdlp:
            need.append("yt-dlp (about 18 MB) - the video download engine")
        if self.ffmpeg is None:
            need.append("ffmpeg (about 90 MB) - joins HD video + audio and makes MP3")
        if messagebox.askyesno(APP_NAME, "MediaGrab needs these free tools (one-time download):\n\n- "
                               + "\n- ".join(need) + "\n\nDownload them now?"):
            self.install_tools()

    def install_tools(self, force_ffmpeg=False):
        if self.tools_busy:
            return
        if not IS_WIN:
            messagebox.showinfo(APP_NAME, "Automatic download is for Windows. Install ffmpeg "
                                "and yt-dlp with your package manager.")
            return
        self.tools_busy = True

        def prog(name):
            def cb(done, total):
                pct = f" {done * 100 // total}%" if total else ""
                self.events.put(("status", f"Downloading {name}...{pct} ({fmt_size(done)})"))
            return cb

        def work():
            try:
                if not find_ytdlp():
                    install_ytdlp(prog("yt-dlp"))
                if force_ffmpeg or find_ffmpeg() is None:
                    install_ffmpeg(prog("ffmpeg"))
                self.events.put(("tools_ok", None))
            except Exception as e:
                self.events.put(("tools_fail", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def startup_updates(self):
        if not self.settings.get("auto_update"):
            return
        self.check_app_update()
        exe = self.ytdlp[0] if self.ytdlp else ""
        own = exe and os.path.dirname(exe) in (app_dir(), BIN_DIR) and exe != sys.executable
        if own and time.time() - self.settings.get("last_ytdlp_update", 0) > 20 * 3600:
            self.tools_busy = True
            self.set_status("Checking for yt-dlp updates...")

            def work():
                try:
                    out = subprocess.run([exe, "-U"], capture_output=True, text=True, timeout=180,
                                         creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
                    text = out.stdout + out.stderr
                    msg = "yt-dlp updated." if "Updated yt-dlp" in text else "yt-dlp is up to date."
                except Exception as e:
                    msg = f"yt-dlp update check failed: {e}"
                self.events.put(("ytdlp_checked", msg))
            threading.Thread(target=work, daemon=True).start()

    def check_app_update(self, manual=False):
        def work():
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                    headers={"User-Agent": f"{APP_NAME}/{VERSION}",
                             "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.load(r)
                tag = data.get("tag_name", "")
                asset = next((a["browser_download_url"] for a in data.get("assets", [])
                              if a["name"].lower().endswith("setup.exe")), None)
                if asset and vtuple(tag) > vtuple(VERSION):
                    self.events.put(("app_update", (tag.lstrip("v"), asset, data.get("html_url", ""))))
                elif manual:
                    self.events.put(("msg", f"You have the latest version ({VERSION})."))
            except Exception as e:
                if manual:
                    self.events.put(("msg", f"Could not check for updates:\n\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def offer_app_update(self, version, asset, page):
        if not (IS_WIN and getattr(sys, "frozen", False)):
            if messagebox.askyesno(APP_NAME, f"{APP_NAME} {version} is available. Open the download page?"):
                webbrowser.open(page)
            return
        if not messagebox.askyesno(APP_NAME, f"A new version of {APP_NAME} ({version}) is available.\n\n"
                                   "Update now? Running downloads will pause and continue after the update."):
            return
        dest = os.path.join(DATA_DIR, f"MediaGrab-{version}-Setup.exe")

        def work():
            try:
                download_file(asset, dest, lambda d, t: self.events.put(
                    ("status", f"Downloading update... {d * 100 // t if t else 0}%")))
                self.events.put(("app_update_ready", dest))
            except Exception as e:
                self.events.put(("msg", f"Update download failed:\n\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def run_installer(self, path):
        self.pause_all_and_save(requeue=True)
        subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
        if self.server:
            self.server.shutdown()
        self.root.destroy()

    def show_extension(self):
        folder = os.path.join(app_dir(), "extension")
        messagebox.showinfo(APP_NAME, "MediaGrab browser extension (Chrome / Edge / Brave):\n"
                            "catches browser downloads, adds Download / MP3 buttons on videos, "
                            "and grabs all links or media from a page.\n\n"
                            "To install it:\n"
                            "1. Open chrome://extensions (or edge://extensions)\n"
                            "2. Turn on Developer mode\n"
                            "3. Click 'Load unpacked' and choose this folder:\n\n" + folder +
                            "\n\nThe folder will open now.")
        if os.path.isdir(folder):
            self.open_path(folder)

    def update_ytdlp(self):
        if not self.ytdlp:
            return self.install_tools()
        if any(self.tasks[i].status in ACTIVE and self.tasks[i].engine == "media" for i in self.order):
            messagebox.showinfo(APP_NAME, "Please wait until the video downloads finish (or stop them), "
                                "then update.")
            return
        if self.ytdlp[0] == sys.executable:
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
        else:
            cmd = [self.ytdlp[0], "-U"]
        self.set_status("Updating yt-dlp...")
        self.tools_busy = True

        def work():
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                     creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
                msg = (out.stdout + out.stderr).strip()[-1500:]
            except Exception as e:
                msg = str(e)
            self.events.put(("ytdlp_checked", "yt-dlp update finished"))
            self.events.put(("msg", "yt-dlp update:\n\n" + (msg or "Done")))
        threading.Thread(target=work, daemon=True).start()

    # ----------------------------------------------------------------- worker
    def start_task(self, t):
        t.status, t.error, t.stop_reason, t.speed, t.eta, t.part = "Connecting", "", None, None, None, ""
        t.last_try = time.time()
        self.refresh_row(t)
        threading.Thread(target=self.run_task, args=(t,), daemon=True).start()

    def run_task(self, t):
        ok = False
        try:
            if t.engine == "auto":
                if is_media_url(t.url, t.preset):
                    t.engine = "media"
                else:
                    try:
                        info = probe(t)
                    except Exception:
                        info = None
                    if info is None or info["type"] in ("text/html", "application/xhtml+xml") \
                            or info["type"].endswith(("mpegurl", "dash+xml")):
                        t.engine = "media"
                    else:
                        t.engine = "file"
                        t.filename = info["filename"]
                        if not t.category or t.category == "Other":
                            t.category = category_of(t.filename)
                        t.probe = info
            elif t.engine == "file":
                t.probe = probe(t)
                t.filename = t.filename or t.probe["filename"]
            if not t.category:
                t.category = self.task_category(t)
            if t.stop_reason:
                raise InterruptedError
            if t.engine == "media":
                if not self.ytdlp:
                    raise RuntimeError("yt-dlp is missing. Use Tools > Update yt-dlp now.")
                t.job = MediaJob(self, t)
            else:
                t.job = FileJob(self, t, getattr(t, "probe"))
            self.events.put(("update", t.id))
            ok = t.job.run()
        except InterruptedError:
            pass
        except FileNotFoundError:
            t.error = "yt-dlp not found. Use Tools > Update yt-dlp now."
        except urllib.error.HTTPError as e:
            t.error = f"Server error {e.code}: {e.reason}"
        except Exception as e:
            t.error = t.error or str(e)
        if t.stop_reason == "pause":
            t.status = "Paused"
        elif t.stop_reason == "cancel":
            t.status = "Cancelled"
        elif ok:
            t.status, t.progress, t.finished = DONE, 100.0, time.time()
        else:
            t.status = "Error"
            t.error = t.error or "Download failed"
        t.speed = t.eta = None
        t.job = None
        self.events.put(("finished", t.id))

    # ----------------------------------------------------------------- loop
    def tick(self):
        changed = set()
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "update":
                    changed.add(data)
                elif kind == "finished":
                    changed.add(data)
                    self.dirty = True
                    t = self.tasks.get(data)
                    if t and t.status == DONE:
                        self.set_status(f"Download complete: {t.name}")
                    elif t and t.status == "Error":
                        self.set_status(f"Failed: {t.name}  (right-click > Show error)")
                elif kind == "add":
                    self.handle_external_add(data)
                elif kind == "status":
                    self.set_status(data)
                elif kind == "msg":
                    self.set_status("Ready")
                    messagebox.showinfo(APP_NAME, data)
                elif kind == "tools_ok":
                    self.tools_busy = False
                    self.ytdlp, self.ffmpeg = find_ytdlp(), find_ffmpeg()
                    self.set_status("Tools ready. You can start downloading.")
                    self.startup_updates()
                elif kind == "ytdlp_checked":
                    self.tools_busy = False
                    self.settings["last_ytdlp_update"] = time.time()
                    self.save_settings()
                    self.set_status(data)
                elif kind == "app_update":
                    self.offer_app_update(*data)
                elif kind == "app_update_ready":
                    self.run_installer(data)
                    return
                elif kind == "tools_fail":
                    self.tools_busy = False
                    self.ytdlp, self.ffmpeg = find_ytdlp(), find_ffmpeg()
                    messagebox.showerror(APP_NAME, "Could not download tools:\n\n" + data +
                                         "\n\nCheck your internet and try Tools > Update yt-dlp now.")
        except queue.Empty:
            pass
        for i in changed:
            if i in self.tasks:
                self.refresh_row(self.tasks[i])

        if not self.tools_busy:
            limit = max(1, self.setting("max_parallel"))
            running = sum(1 for i in self.order if self.tasks[i].status in ACTIVE)
            for i in self.order:
                if running >= limit:
                    break
                t = self.tasks[i]
                if t.status == "Queued":
                    self.start_task(t)
                    running += 1

        for tid, w in list(self.progress_windows.items()):
            if tid in self.tasks and w.winfo_exists():
                w.refresh()
        if self.dirty:
            self.dirty = False
            self.save_queue()
        self.check_clipboard()
        active = [self.tasks[i] for i in self.order if self.tasks[i].status in ACTIVE]
        total_speed = sum(t.speed or 0 for t in active)
        self.speed_var.set(f"{len(active)} active  |  {fmt_size(total_speed) or '0 B'}/sec" if active else "")
        self.root.title(f"{APP_NAME} - {len(active)} downloading - {fmt_size(total_speed)}/s"
                        if active else f"{APP_NAME} Download Manager")
        self.root.after(400, self.tick)

    def check_clipboard(self):
        if not self.settings.get("clipboard"):
            return
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if self.last_clip is None:  # don't react to whatever was copied before start
            self.last_clip = clip
            return
        if clip != self.last_clip:
            self.last_clip = clip
            if URL_RE.match(clip) and (is_media_url(clip) or category_of(clip) != "Other"):
                self.clip_url = clip
                self.set_status(f"Link copied: {clip[:80]}")
                self.clip_btn.pack(side="right", padx=8)

    def download_clip(self):
        self.clip_btn.pack_forget()
        if self.clip_url:
            self.add_dialog({"url": self.clip_url})

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))

    # ----------------------------------------------------------------- link server
    def start_link_server(self):
        LinkHandler.events = self.events
        LinkHandler.info = staticmethod(self.link_info)
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", PORT), LinkHandler)
        except OSError:
            self.server = None
            self.set_status(f"Browser extension link is off (port {PORT} is busy - "
                            f"is {APP_NAME} already open?)")
            return
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def pause_all_and_save(self, requeue=False):
        for i in self.order:
            t = self.tasks[i]
            if t.status in ACTIVE:
                t.stop_reason = "pause"
                if t.job:
                    t.job.stop()
                t.status = "Queued" if requeue else "Paused"
        self.save_settings()
        self.save_queue()

    def on_close(self):
        active = [self.tasks[i] for i in self.order if self.tasks[i].status in ACTIVE]
        if active and not messagebox.askyesno(
                APP_NAME, f"{len(active)} download(s) running. They will be paused and can be "
                "resumed next time. Exit?"):
            return
        self.pause_all_and_save()
        time.sleep(0.3)
        if self.server:
            self.server.shutdown()
        self.root.destroy()


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    ico = resource("mediagrab.ico")
    if ico and IS_WIN:
        try:
            root.iconbitmap(ico)
        except tk.TclError:
            pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
