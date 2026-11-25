#!/usr/bin/env python3
"""
MediaGrab - a simple IDM-style video & audio downloader.

Built on yt-dlp (supports YouTube, Facebook, Instagram, TikTok, X/Twitter and
1000+ other sites) and ffmpeg (merging HD video + audio, converting to MP3).

Features: download queue, parallel downloads, pause / resume, quality picker,
audio-only (MP3 / M4A), playlists, clipboard link catcher, and a local link
(127.0.0.1:9614) that the MediaGrab browser extension uses to send videos.

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
import urllib.request
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "MediaGrab"
VERSION = "1.1.0"
UPDATE_REPO = "eapasindu-sudo/MediaGrab"  # GitHub repo that publishes new versions
PORT = 9614
IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")

PRESETS = [
    "Best video",
    "2160p (4K)",
    "1440p",
    "1080p",
    "720p",
    "480p",
    "360p",
    "Audio: MP3",
    "Audio: M4A",
]
# Short names the browser extension may send
PRESET_ALIASES = {
    "best": "Best video", "2160": "2160p (4K)", "4k": "2160p (4K)", "1440": "1440p",
    "1080": "1080p", "720": "720p", "480": "480p", "360": "360p",
    "mp3": "Audio: MP3", "m4a": "Audio: M4A", "audio": "Audio: MP3",
}
COOKIE_BROWSERS = ["(none)", "firefox", "chrome", "edge", "brave", "opera", "vivaldi"]
ACTIVE = ("Starting", "Downloading", "Processing")
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


def find_ytdlp():
    """Return the command (list) used to run yt-dlp, or None."""
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


# --------------------------------------------------------------------------- formatting

def vtuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s))[:3])


def resource(name):
    """Path of a file bundled next to the app (or inside the PyInstaller exe)."""
    for d in (getattr(sys, "_MEIPASS", ""), app_dir()):
        p = os.path.join(d, name)
        if d and os.path.exists(p):
            return p
    return None


def fmt_size(n):
    if not n:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_eta(s):
    if s is None:
        return ""
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


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


def format_args(preset, has_ffmpeg):
    """yt-dlp arguments that pick the format for a quality preset."""
    if preset.startswith("Audio"):
        if not has_ffmpeg:  # cannot convert: just grab the best audio file as-is
            return ["-f", "ba[ext=m4a]/ba/b"]
        if "MP3" in preset:
            return ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
        return ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a"]
    m = re.match(r"(\d+)p", preset)
    h = int(m.group(1)) if m else None
    if has_ffmpeg:
        sort = f"res:{h},ext:mp4:m4a" if h else "res,ext:mp4:m4a"
        return ["-f", "bv*+ba/b", "-S", sort, "--merge-output-format", "mp4"]
    # without ffmpeg only single-file formats (video+audio together) can be used
    return ["-f", f"b[height<={h}]/b" if h else "b"]


# --------------------------------------------------------------------------- task

class Task:
    FIELDS = ("id", "url", "referer", "title", "preset", "playlist", "folder", "status",
              "progress", "size", "error", "filepath", "added")

    def __init__(self, url, preset, folder, playlist=False, referer="", title=""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.referer = referer or ""
        self.title = title or url
        self.preset = preset
        self.playlist = playlist
        self.folder = folder
        self.status = "Queued"
        self.progress = 0.0
        self.size = 0
        self.speed = None
        self.eta = None
        self.part = ""
        self.error = ""
        self.filepath = ""
        self.added = time.time()
        self.proc = None
        self.stop_reason = None

    def to_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    @classmethod
    def from_dict(cls, d):
        t = cls(d["url"], d.get("preset", PRESETS[0]), d.get("folder", ""))
        for k in cls.FIELDS:
            if k in d:
                setattr(t, k, d[k])
        if t.status in ACTIVE:  # app was closed mid-download -> resumable
            t.status = "Paused"
        return t


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


# --------------------------------------------------------------------------- browser link

class LinkHandler(BaseHTTPRequestHandler):
    """Tiny local API used by the browser extension. Only accepts requests from
    browser extensions (or local programs), never from normal web pages."""
    events = None
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
            return self._reply(200, {"app": APP_NAME, "version": VERSION, "presets": PRESETS})
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not (self._host_ok() and self._origin_ok()):
            return self._reply(403, {"error": "forbidden"})
        if self.path != "/add":
            return self._reply(404, {"error": "not found"})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._reply(415, {"error": "json only"})
        length = min(int(self.headers.get("Content-Length") or 0), 65536)
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
        }))
        self._reply(200, {"ok": True})


# --------------------------------------------------------------------------- app

class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.tasks = {}
        self.order = []
        self.ytdlp = find_ytdlp()
        self.ffmpeg = find_ffmpeg()
        self.tools_busy = False
        self.last_clip = ""
        self.dirty = False
        self.settings = self.load_settings()

        root.title(f"{APP_NAME} - Video & Audio Downloader")
        root.geometry("1000x600")
        root.minsize(760, 420)
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
            "preset": PRESETS[0],
            "max_parallel": 2,
            "cookies_browser": "(none)",
            "cookies_file": "",
            "clipboard": True,
            "playlist": False,
            "speed_limit": 0,
            "auto_update": True,
            "last_ytdlp_update": 0,
        }
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s.update(json.load(f))
        except (OSError, ValueError):
            pass
        return s

    def save_settings(self):
        self.settings.update({
            "folder": self.folder_var.get(),
            "preset": self.preset_var.get(),
            "max_parallel": self.safe_int(self.parallel_var.get(), 2),
            "cookies_browser": self.cookie_var.get(),
            "clipboard": self.clip_var.get(),
            "playlist": self.playlist_var.get(),
            "speed_limit": self.safe_int(self.limit_var.get(), 0),
            "auto_update": self.auto_var.get(),
        })
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

    def load_queue(self):
        try:
            with open(QUEUE_FILE, encoding="utf-8") as f:
                for d in json.load(f):
                    t = Task.from_dict(d)
                    self.tasks[t.id] = t
                    self.order.append(t.id)
                    self.insert_row(t)
        except (OSError, ValueError, KeyError):
            pass

    def save_queue(self):
        try:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump([self.tasks[i].to_dict() for i in self.order], f, indent=1)
        except OSError:
            pass

    # ----------------------------------------------------------------- UI
    def build_ui(self):
        r = self.root
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=24)

        menubar = tk.Menu(r)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Add links from text...", command=self.add_many_dialog)
        m_file.add_command(label="Open download folder", command=self.open_folder)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=m_file)
        m_tools = tk.Menu(menubar, tearoff=0)
        m_tools.add_command(label="Update yt-dlp (fix broken sites)", command=self.update_ytdlp)
        m_tools.add_command(label="Download / repair ffmpeg", command=lambda: self.install_tools(force_ffmpeg=True))
        m_tools.add_command(label="Choose cookies.txt file...", command=self.choose_cookies_file)
        m_tools.add_command(label="Clear cookies.txt file", command=self.clear_cookies_file)
        m_tools.add_separator()
        self.auto_var = tk.BooleanVar(value=self.settings.get("auto_update", True))
        m_tools.add_checkbutton(label="Update automatically", variable=self.auto_var,
                                command=self.save_settings)
        menubar.add_cascade(label="Tools", menu=m_tools)
        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="Supported sites", command=lambda: webbrowser.open(
            "https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"))
        m_help.add_command(label="Install browser extension...", command=self.show_extension)
        m_help.add_command(label="Check for updates", command=lambda: self.check_app_update(manual=True))
        m_help.add_command(label="About", command=self.about)
        menubar.add_cascade(label="Help", menu=m_help)
        r.config(menu=menubar)

        top = ttk.Frame(r, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Link:").pack(side="left")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(top, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.url_entry.bind("<Return>", lambda e: self.add_from_entry())
        ttk.Button(top, text="Paste", command=self.paste).pack(side="left")
        ttk.Button(top, text="Download", command=self.add_from_entry).pack(side="left", padx=(6, 0))

        opts = ttk.Frame(r, padding=(10, 0, 10, 6))
        opts.pack(fill="x")
        ttk.Label(opts, text="Quality:").pack(side="left")
        self.preset_var = tk.StringVar(value=self.settings["preset"]
                                       if self.settings["preset"] in PRESETS else PRESETS[0])
        ttk.Combobox(opts, textvariable=self.preset_var, values=PRESETS, state="readonly",
                     width=13).pack(side="left", padx=(4, 12))
        self.playlist_var = tk.BooleanVar(value=self.settings["playlist"])
        ttk.Checkbutton(opts, text="Whole playlist", variable=self.playlist_var).pack(side="left")
        ttk.Label(opts, text="Save to:").pack(side="left", padx=(12, 0))
        self.folder_var = tk.StringVar(value=self.settings["folder"])
        ttk.Entry(opts, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(opts, text="Browse...", command=self.browse).pack(side="left")

        mid = ttk.Frame(r, padding=(10, 0, 10, 0))
        mid.pack(fill="both", expand=True)
        cols = ("name", "quality", "size", "progress", "speed", "eta", "status")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, text, w, anchor in (
            ("name", "File name", 360, "w"), ("quality", "Quality", 90, "center"),
            ("size", "Size", 80, "e"), ("progress", "Progress", 80, "e"),
            ("speed", "Speed", 90, "e"), ("eta", "Time left", 70, "e"),
            ("status", "Status", 150, "w"),
        ):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "name"))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.open_file())
        self.tree.bind("<Button-3>", self.popup_menu)
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_detail())
        for st, color in (("Done", "#1a7f37"), ("Error", "#c62828"), ("Paused", "#8a6d00"),
                          ("Cancelled", "#777777")):
            self.tree.tag_configure(st, foreground=color)

        self.menu = tk.Menu(r, tearoff=0)
        self.menu.add_command(label="Open file", command=self.open_file)
        self.menu.add_command(label="Open folder", command=self.open_folder)
        self.menu.add_separator()
        self.menu.add_command(label="Pause", command=self.pause)
        self.menu.add_command(label="Resume / Retry", command=self.resume)
        self.menu.add_command(label="Cancel", command=self.cancel)
        self.menu.add_command(label="Remove from list", command=self.remove)
        self.menu.add_separator()
        self.menu.add_command(label="Copy link", command=self.copy_link)
        self.menu.add_command(label="Show error", command=self.show_error)

        detail = ttk.Frame(r, padding=(10, 6, 10, 0))
        detail.pack(fill="x")
        self.detail_bar = ttk.Progressbar(detail, maximum=100)
        self.detail_bar.pack(fill="x")
        self.detail_lbl = ttk.Label(detail, text="", foreground="#555")
        self.detail_lbl.pack(fill="x", pady=(2, 0))

        bottom = ttk.Frame(r, padding=(10, 6, 10, 4))
        bottom.pack(fill="x")
        for text, cmd in (("Pause", self.pause), ("Resume", self.resume), ("Cancel", self.cancel),
                          ("Remove", self.remove), ("Clear finished", self.clear_finished),
                          ("Open folder", self.open_folder)):
            ttk.Button(bottom, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Label(bottom, text="Max parallel:").pack(side="left", padx=(12, 2))
        self.parallel_var = tk.StringVar(value=str(self.settings["max_parallel"]))
        ttk.Spinbox(bottom, from_=1, to=8, width=3, textvariable=self.parallel_var).pack(side="left")
        ttk.Label(bottom, text="Speed limit KB/s (0=off):").pack(side="left", padx=(12, 2))
        self.limit_var = tk.StringVar(value=str(self.settings["speed_limit"]))
        ttk.Entry(bottom, textvariable=self.limit_var, width=7).pack(side="left")

        bottom2 = ttk.Frame(r, padding=(10, 0, 10, 4))
        bottom2.pack(fill="x")
        ttk.Label(bottom2, text="Login cookies from browser (for private FB/IG videos):").pack(side="left")
        self.cookie_var = tk.StringVar(value=self.settings["cookies_browser"])
        ttk.Combobox(bottom2, textvariable=self.cookie_var, values=COOKIE_BROWSERS,
                     state="readonly", width=10).pack(side="left", padx=4)
        self.clip_var = tk.BooleanVar(value=self.settings["clipboard"])
        ttk.Checkbutton(bottom2, text="Catch links I copy", variable=self.clip_var).pack(side="left", padx=12)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(r, textvariable=self.status_var, relief="sunken", anchor="w",
                  padding=(6, 2)).pack(fill="x", side="bottom")

    def set_status(self, text):
        self.status_var.set(text)

    # ----------------------------------------------------------------- rows
    def row_values(self, t):
        if t.status in ("Downloading", "Starting", "Processing"):
            speed = (fmt_size(t.speed) + "/s") if t.speed else ""
            eta = fmt_eta(t.eta)
        else:
            speed = eta = ""
        status = t.status + (f" {t.part}" if t.part and t.status == "Downloading" else "")
        if t.status == "Error" and t.error:
            status = "Error: " + t.error[:80]
        return (t.title, t.preset, fmt_size(t.size), f"{t.progress:.1f}%", speed, eta, status)

    def insert_row(self, t):
        self.tree.insert("", "end", iid=t.id, values=self.row_values(t), tags=(t.status,))

    def refresh_row(self, t):
        if self.tree.exists(t.id):
            self.tree.item(t.id, values=self.row_values(t), tags=(t.status,))

    def selected(self):
        return [self.tasks[i] for i in self.tree.selection() if i in self.tasks]

    def update_detail(self):
        sel = self.selected()
        if not sel:
            active = [self.tasks[i] for i in self.order if self.tasks[i].status in ACTIVE]
            sel = active[:1]
        if not sel:
            self.detail_bar["value"] = 0
            self.detail_lbl.config(text="")
            return
        t = sel[0]
        self.detail_bar["value"] = t.progress
        info = f"{t.title}  -  {t.status}"
        if t.filepath:
            info += f"  -  {t.filepath}"
        elif t.error and t.status == "Error":
            info += f"  -  {t.error}"
        self.detail_lbl.config(text=info)

    # ----------------------------------------------------------------- adding
    def paste(self):
        try:
            self.url_var.set(self.root.clipboard_get().strip())
        except tk.TclError:
            pass

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or os.path.expanduser("~"))
        if d:
            self.folder_var.set(d)
            self.save_settings()

    def add_from_entry(self):
        text = self.url_var.get().strip()
        urls = [u for u in re.split(r"\s+", text) if URL_RE.match(u)]
        if not urls:
            messagebox.showwarning(APP_NAME, "Please paste a video or audio link (starting with http).")
            return
        for u in urls:
            self.add_task(u)
        self.url_var.set("")
        self.save_settings()

    def add_many_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Add links")
        win.geometry("560x320")
        ttk.Label(win, text="Paste links, one per line:", padding=8).pack(anchor="w")
        txt = tk.Text(win, height=12)
        txt.pack(fill="both", expand=True, padx=8)

        def ok():
            for u in re.split(r"\s+", txt.get("1.0", "end")):
                if URL_RE.match(u):
                    self.add_task(u)
            win.destroy()
        ttk.Button(win, text="Add all", command=ok).pack(pady=8)

    def add_task(self, url, preset=None, referer="", title=""):
        folder = self.folder_var.get().strip() or self.settings["folder"]
        t = Task(url, preset or self.preset_var.get(), folder,
                 playlist=self.playlist_var.get(), referer=referer, title=title)
        self.tasks[t.id] = t
        self.order.append(t.id)
        self.insert_row(t)
        self.tree.see(t.id)
        self.save_queue()
        self.set_status(f"Added: {url}")
        return t

    # ----------------------------------------------------------------- actions
    def pause(self):
        for t in self.selected():
            if t.status in ACTIVE:
                t.stop_reason = "pause"
                kill_tree(t.proc)
            elif t.status == "Queued":
                t.status = "Paused"
                self.refresh_row(t)
        self.save_queue()

    def resume(self):
        for t in self.selected():
            if t.status in ("Paused", "Error", "Cancelled"):
                t.status, t.error, t.stop_reason = "Queued", "", None
                self.refresh_row(t)
        self.save_queue()

    def cancel(self):
        for t in self.selected():
            if t.status in ACTIVE:
                t.stop_reason = "cancel"
                kill_tree(t.proc)
            elif t.status in ("Queued", "Paused"):
                t.status = "Cancelled"
                self.refresh_row(t)
        self.save_queue()

    def remove(self):
        sel = self.selected()
        if any(t.status in ACTIVE for t in sel):
            if not messagebox.askyesno(APP_NAME, "Some selected downloads are running. Stop and remove them?"):
                return
        for t in sel:
            if t.status in ACTIVE:
                t.stop_reason = "cancel"
                kill_tree(t.proc)
            self.tree.delete(t.id)
            self.order.remove(t.id)
            del self.tasks[t.id]
        self.save_queue()
        self.update_detail()

    def clear_finished(self):
        for i in list(self.order):
            if self.tasks[i].status in ("Done", "Cancelled"):
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
            self.open_folder()

    def open_folder(self):
        sel = self.selected()
        folder = sel[0].folder if sel else self.folder_var.get()
        if sel and sel[0].filepath and os.path.exists(sel[0].filepath):
            if IS_WIN:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(sel[0].filepath)])
                return
            folder = os.path.dirname(sel[0].filepath)
        os.makedirs(folder, exist_ok=True)
        self.open_path(folder)

    def popup_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    def choose_cookies_file(self):
        p = filedialog.askopenfilename(title="Choose cookies.txt (Netscape format)",
                                       filetypes=[("Cookies", "*.txt"), ("All files", "*.*")])
        if p:
            self.settings["cookies_file"] = p
            self.save_settings()
            self.set_status(f"Using cookies file: {p}")

    def clear_cookies_file(self):
        self.settings["cookies_file"] = ""
        self.save_settings()
        self.set_status("Cookies file cleared")

    def about(self):
        messagebox.showinfo(APP_NAME, f"{APP_NAME} {VERSION}\n\n"
                            "Video & audio downloader powered by yt-dlp and ffmpeg.\n\n"
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
            need.append("yt-dlp (about 18 MB) - the download engine")
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

    # ----------------------------------------------------------------- auto-update
    def startup_updates(self):
        if not self.auto_var.get():
            return
        self.check_app_update()
        # keep the download engine fresh (sites like YouTube change often): once a day
        exe = self.ytdlp[0] if self.ytdlp else ""
        own = exe and os.path.dirname(exe) in (app_dir(), BIN_DIR) and exe != sys.executable
        if own and time.time() - self.settings.get("last_ytdlp_update", 0) > 20 * 3600:
            self.tools_busy = True  # downloads wait until the update is done
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
                         creationflags=0x00000008 | 0x00000200, close_fds=True)  # detached
        if self.server:
            self.server.shutdown()
        self.root.destroy()

    def show_extension(self):
        folder = os.path.join(app_dir(), "extension")  # installed next to MediaGrab.exe
        messagebox.showinfo(APP_NAME, "To add the MediaGrab buttons to Chrome / Edge / Brave:\n\n"
                            "1. Open chrome://extensions (or edge://extensions)\n"
                            "2. Turn on Developer mode\n"
                            "3. Click 'Load unpacked' and choose this folder:\n\n" + folder +
                            "\n\nThe folder will open now.")
        if os.path.isdir(folder):
            self.open_path(folder)

    def update_ytdlp(self):
        if not self.ytdlp:
            return self.install_tools()
        if self.ytdlp[0] == sys.executable:
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
        else:
            cmd = [self.ytdlp[0], "-U"]
        self.set_status("Updating yt-dlp...")

        def work():
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                     creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
                msg = (out.stdout + out.stderr).strip()[-1500:]
            except Exception as e:
                msg = str(e)
            self.events.put(("msg", "yt-dlp update:\n\n" + (msg or "Done")))
        threading.Thread(target=work, daemon=True).start()

    # ----------------------------------------------------------------- download worker
    def build_cmd(self, t):
        has_ff = self.ffmpeg is not None
        progress_tpl = ("download:PROG|%(progress.status)s|%(progress.downloaded_bytes)s|"
                        "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
                        "%(progress.speed)s|%(progress.eta)s")
        if t.playlist:
            out = "%(playlist_title|Playlist).80B/%(playlist_index|0)03d - %(title).150B [%(id)s].%(ext)s"
        else:
            out = "%(title).150B [%(id)s].%(ext)s"
        cmd = list(self.ytdlp) + [
            "--newline", "--progress", "--color", "never", "--encoding", "utf-8",
            "--progress-template", progress_tpl,
            "--print", "before_dl:TITLE|%(playlist_index|)s|%(n_entries|)s|%(title)s",
            "--print", "after_move:FILE|%(filepath)s",
            "--no-simulate", "--no-mtime", "--windows-filenames",
            "--concurrent-fragments", "4", "--retries", "10", "--fragment-retries", "10",
            "-P", t.folder, "-o", out,
        ]
        cmd += format_args(t.preset, has_ff)
        if has_ff and self.ffmpeg:
            cmd += ["--ffmpeg-location", self.ffmpeg]
        if not t.playlist:
            cmd.append("--no-playlist")
        if t.referer:
            cmd += ["--referer", t.referer]
        limit = self.safe_int(self.limit_var.get(), 0)
        if limit:
            cmd += ["-r", f"{limit}K"]
        browser = self.cookie_var.get()
        if browser and browser != "(none)":
            cmd += ["--cookies-from-browser", browser]
        elif self.settings.get("cookies_file"):
            cmd += ["--cookies", self.settings["cookies_file"]]
        cmd += ["--", t.url]
        return cmd

    def start_task(self, t):
        t.status, t.error, t.stop_reason, t.speed, t.eta, t.part = "Starting", "", None, None, None, ""
        self.refresh_row(t)
        os.makedirs(t.folder, exist_ok=True)
        cmd = self.build_cmd(t)
        threading.Thread(target=self.run_task, args=(t, cmd), daemon=True).start()

    def run_task(self, t, cmd):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        kw = {"creationflags": NO_WINDOW} if IS_WIN else {"start_new_session": True}
        last_lines = []
        file_index = 0
        try:
            t.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                      errors="replace", bufsize=1, env=env, **kw)
            for line in t.proc.stdout:
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
                        t.progress = min(100.0, (done or 0) * 100 / total)
                    t.speed, t.eta = speed, eta
                    if p[1] == "finished":
                        t.status = "Processing"
                elif line.startswith("TITLE|"):
                    _, idx, n, title = line.split("|", 3)
                    t.title = title + (f"  [{idx}/{n}]" if idx and n and t.playlist else "")
                    file_index = 0
                    t.part = ""
                    t.status = "Downloading"
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
                self.events.put(("update", t.id))
            rc = t.proc.wait()
        except FileNotFoundError:
            rc, t.error = -1, "yt-dlp not found. Use Tools > Update yt-dlp."
        except Exception as e:
            rc, t.error = -1, str(e)
        if t.stop_reason == "pause":
            t.status = "Paused"
        elif t.stop_reason == "cancel":
            t.status = "Cancelled"
        elif rc == 0:
            t.status, t.progress = "Done", 100.0
        else:
            t.status = "Error"
            if not t.error:
                t.error = " | ".join(last_lines[-3:]) or f"yt-dlp exited with code {rc}"
        t.speed = t.eta = None
        t.proc = None
        self.events.put(("finished", t.id))

    # ----------------------------------------------------------------- loop
    def tick(self):
        try:
            changed = set()
            while True:
                kind, data = self.events.get_nowait()
                if kind == "update":
                    changed.add(data)
                elif kind == "finished":
                    changed.add(data)
                    self.dirty = True
                    t = self.tasks.get(data)
                    if t and t.status == "Done":
                        self.set_status(f"Finished: {t.title}")
                    elif t and t.status == "Error":
                        self.set_status(f"Failed: {t.title}  (right-click > Show error)")
                elif kind == "add":
                    self.add_task(data["url"], normalize_preset(data.get("quality")),
                                  data.get("referer", ""), data.get("title", ""))
                    self.bring_to_front()
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
                                         "\n\nCheck your internet and try Tools > Update yt-dlp.")
        except queue.Empty:
            pass
        for i in changed:
            if i in self.tasks:
                self.refresh_row(self.tasks[i])

        # scheduler
        if self.ytdlp and not self.tools_busy:
            limit = max(1, self.safe_int(self.parallel_var.get(), 2))
            running = sum(1 for i in self.order if self.tasks[i].status in ACTIVE)
            for i in self.order:
                if running >= limit:
                    break
                t = self.tasks[i]
                if t.status == "Queued":
                    self.start_task(t)
                    running += 1

        if self.dirty:
            self.dirty = False
            self.save_queue()
        self.update_detail()
        self.check_clipboard()
        active = [self.tasks[i] for i in self.order if self.tasks[i].status in ACTIVE]
        total_speed = sum(t.speed or 0 for t in active)
        self.root.title(f"{APP_NAME} - {len(active)} downloading - {fmt_size(total_speed)}/s"
                        if active else f"{APP_NAME} - Video & Audio Downloader")
        self.root.after(400, self.tick)

    def check_clipboard(self):
        if not self.clip_var.get():
            return
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if clip != self.last_clip:
            self.last_clip = clip
            if URL_RE.match(clip) and not self.url_var.get().strip():
                self.url_var.set(clip)
                self.set_status("Link caught from clipboard - press Download to get it.")

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))

    # ----------------------------------------------------------------- link server
    def start_link_server(self):
        LinkHandler.events = self.events
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
                kill_tree(t.proc)
                t.status = "Queued" if requeue else "Paused"  # Queued = continue after update
        self.save_settings()
        self.save_queue()

    def on_close(self):
        active = [self.tasks[i] for i in self.order if self.tasks[i].status in ACTIVE]
        if active and not messagebox.askyesno(
                APP_NAME, f"{len(active)} download(s) running. They will be paused and can be "
                "resumed next time. Exit?"):
            return
        self.pause_all_and_save()
        if self.server:
            self.server.shutdown()
        self.root.destroy()


def main():
    if IS_WIN:
        try:  # crisp text on high-DPI screens
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
