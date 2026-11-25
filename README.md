# MediaGrab: a free IDM-style video and audio downloader

MediaGrab downloads video and audio from YouTube, Facebook, Instagram, TikTok, X (Twitter), Vimeo, SoundCloud and [1000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). It also catches plain video/audio files and streams (MP4, M3U8, MP3 and more) on any website, so if a page plays media, you can usually download it.

It is built on two free tools: **yt-dlp** (the download engine) and **ffmpeg** (joins HD video with its audio and makes MP3s). MediaGrab downloads both for you the first time it starts.

> **For personal use only.** Download only content you have the right to save, and respect each website's terms of service and copyright law.

## What it can do

- Paste a link and press **Download**. Or just copy a link: MediaGrab catches it from the clipboard.
- Quality: Best, 4K, 1440p, 1080p, 720p, 480p, 360p, or **audio only (MP3 / M4A)**.
- Download queue with several downloads at the same time (you choose how many).
- **Pause / Resume**: stopped downloads continue from where they stopped, even after closing the app.
- Whole YouTube playlists (tick "Whole playlist").
- Speed limit, so downloads don't use all your internet.
- Browser extension (Chrome, Edge, Brave): a button and right-click menu that send the video on the page to MediaGrab, plus a list of media files found on the page, like IDM.
- Private Facebook / Instagram videos: choose your browser under "Login cookies from browser" so MediaGrab can use your login.

## Install (easiest)

Download **`MediaGrab-Setup.exe`** from the [latest release](https://github.com/eapasindu-sudo/MediaGrab/releases/latest) and run it. It installs MediaGrab with yt-dlp and ffmpeg included, so there's nothing else to install, and you don't need admin rights.

**Automatic updates:** when MediaGrab starts, it checks for a new version of itself and asks you to update (one click; your downloads continue afterwards). It also updates yt-dlp once a day, so YouTube, Facebook and Instagram keep working. You can turn this off under **Tools > Update automatically**.

## Option A: build MediaGrab.exe yourself

1. Install Python from <https://www.python.org/downloads/>. In the installer, tick **"Add python.exe to PATH"**.
2. Put this `MediaGrab` folder anywhere on your PC (for example `Documents\MediaGrab`).
3. Double-click **`build_windows.bat`**. Wait until it says "Done!".
4. Your app is in `dist\MediaGrab.exe`. Copy it anywhere (Desktop is fine) and make a shortcut if you like.
5. Start it. The first time, click **Yes** to download yt-dlp and ffmpeg (about 110 MB, one time only).

## Option B: run without building

Install Python (step 1 above), then double-click **`run_mediagrab.bat`**.

## Install the browser extension (Chrome / Edge / Brave)

In MediaGrab, open **Help > Install browser extension...** to see the folder, or use the `extension` folder from this download.

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top right in Chrome, left side in Edge).
3. Click **Load unpacked** and choose the `extension` folder inside `MediaGrab`.
4. Pin the blue MediaGrab icon to the toolbar.

How to use it (keep the MediaGrab app open):

- **While a video plays** (YouTube, Facebook, Instagram or any site), blue **Download** and green **MP3** buttons appear at the top-right of the video. Click **MP3** to convert that video to MP3 and download it. Click **x** to hide the buttons on that page.
- Or click the MediaGrab icon in the toolbar, choose quality, then **Download video from this page**.
- Right-click a video, a link or the page and choose **Download ... with MediaGrab** (or **Download as MP3**).
- On other sites: play the video, then click the icon. The number on the icon shows media files it found; press **Get** next to one.

## Tips and fixes

| Problem | Fix |
| --- | --- |
| A site suddenly stops working (YouTube changes often) | **Tools > Update yt-dlp**. Do this first for almost any error. |
| Facebook / Instagram says login required or private | Set **Login cookies from browser** to the browser where you are logged in. **Firefox works best**; Chrome/Edge sometimes lock their cookies while open. Close that browser and try again, or export a `cookies.txt` with a cookies.txt extension and use **Tools > Choose cookies.txt file**. |
| HD video has no sound, or MP3 is not made | ffmpeg is missing. Use **Tools > Download / repair ffmpeg**. |
| Extension says "app not running" | Open MediaGrab first. Only one copy of MediaGrab can run at once. |
| See why a download failed | Right-click it > **Show error**. |
| Windows SmartScreen warns about MediaGrab.exe | It's because the exe you built isn't signed. Click **More info > Run anyway**. |

Netflix, Disney+, Spotify and other DRM-protected services cannot be downloaded. That's by design.

## Where things are saved

- Downloads: `Downloads\MediaGrab` (change it with **Browse...**).
- Settings, queue, yt-dlp and ffmpeg: `%LOCALAPPDATA%\MediaGrab`.

## Files

| File | What it is |
| --- | --- |
| `mediagrab.py` | The app (Python + Tkinter, no extra libraries needed to run) |
| `build_windows.bat` | Builds `dist\MediaGrab.exe` |
| `run_mediagrab.bat` | Runs the app without building |
| `extension\` | Browser extension (load unpacked) |
| `installer.iss` | Inno Setup script for `MediaGrab-Setup.exe` |
| `.github/workflows/release.yml` | Builds the installer on GitHub. Raise `VERSION` in `mediagrab.py` and push to `main` to publish an update; installed copies update themselves. |

The extension talks to the app only on your own computer (`127.0.0.1:9614`). The app ignores requests from normal websites, so a web page can't add downloads by itself.
