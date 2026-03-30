#!/usr/bin/env python3
"""
sc_rip_gui.py — SoundCloud ripper with native macOS GUI
requires: yt-dlp, mutagen, requests, Pillow  (tkinter ships with Python)
    pip install yt-dlp mutagen requests Pillow

Run:
    python sc_rip_gui.py
"""

import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests

# ---------------------------------------------------------------------------
# sc_rip core (inline so this file is self-contained)
# ---------------------------------------------------------------------------

SC_HOME = "https://soundcloud.com"
SC_API  = "https://api-v2.soundcloud.com"
_client_id_cache: str | None = None


def _scrape_client_id() -> str:
    global _client_id_cache
    if _client_id_cache:
        return _client_id_cache
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    html = requests.get(SC_HOME, headers=headers, timeout=15).text
    script_urls = re.findall(
        r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html
    )
    for url in reversed(script_urls):
        try:
            js = requests.get(url, headers=headers, timeout=10).text
            m = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', js)
            if m:
                _client_id_cache = m.group(1)
                return _client_id_cache
        except Exception:
            continue
    raise RuntimeError("Could not extract client_id from SoundCloud.")


def _api_get(path, client_id, oauth=None, **params):
    headers = {"Accept": "application/json"}
    if oauth:
        headers["Authorization"] = oauth
    resp = requests.get(
        f"{SC_API}/{path.lstrip('/')}",
        params={"client_id": client_id, **params},
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def resolve_url(url, client_id, oauth=None):
    return _api_get("resolve", client_id, oauth, url=url)


def fetch_user_tracks(user_id, client_id, oauth=None):
    tracks, next_href = [], None
    while True:
        if next_href:
            resp = requests.get(
                next_href,
                params={"client_id": client_id},
                headers={"Authorization": oauth} if oauth else {},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        else:
            data = _api_get(
                f"users/{user_id}/tracks", client_id, oauth,
                limit=50, linked_partitioning=1
            )
        tracks.extend(data.get("collection", []))
        next_href = data.get("next_href")
        if not next_href:
            break
        time.sleep(0.3)
    return tracks


def _download_image(url, dest, session, fallback=None):
    for attempt in ([url, fallback] if fallback and fallback != url else [url]):
        if not attempt:
            continue
        try:
            r = session.get(attempt, timeout=15)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                return True
        except Exception:
            pass
    return False


def download_artist_profile(user, out_dir, session):
    profile = {k: user.get(k) for k in (
        "id", "permalink", "username", "full_name", "description",
        "city", "country_code", "followers_count", "followings_count",
        "track_count", "likes_count", "reposts_count", "verified",
        "website", "website_title", "permalink_url", "avatar_url", "visuals",
    )}
    (out_dir / "artist_profile.json").write_text(
        json.dumps(profile, indent=2, ensure_ascii=False)
    )
    avatar_url = user.get("avatar_url")
    if avatar_url:
        full = re.sub(r"-large\.jpg", "-original.jpg", avatar_url)
        _download_image(full, out_dir / "avatar_original.jpg", session, fallback=avatar_url)
    visuals = user.get("visuals") or {}
    for vis in visuals.get("visuals", []):
        if vis.get("visual_url"):
            _download_image(vis["visual_url"], out_dir / "banner.jpg", session)
            break


def _safe_dirname(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _ydl_opts(out_dir, audio_format, oauth, log_fn):
    class Logger:
        def debug(self, msg):
            if not msg.startswith("[debug]"):
                log_fn(msg)
        def warning(self, msg): log_fn(f"[warn] {msg}")
        def error(self, msg):   log_fn(f"[err]  {msg}")

    opts = {
        "format":        "bestaudio/best",
        "outtmpl":       str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "writethumbnail": True,
        "embedthumbnail": True,
        "addmetadata":   True,
        "writeinfojson": True,
        "logger":        Logger(),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": audio_format, "preferredquality": "0"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
    }
    if oauth:
        opts["http_headers"] = {"Authorization": oauth}
    return opts


def rip(url, out_dir, audio_format, oauth, log_fn):
    log_fn("Scraping client_id...")
    client_id = _scrape_client_id()
    log_fn(f"client_id acquired: {client_id[:8]}...")

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    log_fn(f"Resolving: {url}")
    resource = resolve_url(url, client_id, oauth)
    kind = resource.get("kind")
    log_fn(f"Resource type: {kind}")

    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError("yt-dlp not installed. Run: pip install yt-dlp")

    if kind == "user":
        user = resource
        artist_dir = out_dir / _safe_dirname(user.get("permalink", "artist"))
        artist_dir.mkdir(parents=True, exist_ok=True)
        log_fn(f"Saving artist profile for {user.get('username')}...")
        download_artist_profile(user, artist_dir, session)
        tracks = fetch_user_tracks(user["id"], client_id, oauth)
        log_fn(f"Found {len(tracks)} tracks")
        track_urls = [t["permalink_url"] for t in tracks if t.get("permalink_url")]
        with YoutubeDL(_ydl_opts(artist_dir, audio_format, oauth, log_fn)) as ydl:
            for u in track_urls:
                log_fn(f"Downloading: {u}")
                ydl.download([u])

    elif kind == "playlist":
        owner = resource.get("user", {})
        owner_dir = out_dir / _safe_dirname(owner.get("permalink", "artist"))
        pl_dir = owner_dir / _safe_dirname(resource.get("permalink", "playlist"))
        pl_dir.mkdir(parents=True, exist_ok=True)
        pl_meta = {k: resource.get(k) for k in (
            "id", "title", "description", "track_count", "genre",
            "artwork_url", "permalink_url", "created_at",
        )}
        pl_meta["owner"] = {k: owner.get(k) for k in ("id", "username", "permalink", "avatar_url")}
        (pl_dir / "playlist_metadata.json").write_text(json.dumps(pl_meta, indent=2, ensure_ascii=False))
        art = resource.get("artwork_url")
        if art:
            _download_image(re.sub(r"-large\.jpg", "-t500x500.jpg", art), pl_dir / "artwork.jpg", session, fallback=art)
        if owner.get("id"):
            download_artist_profile(_api_get(f"users/{owner['id']}", client_id, oauth), owner_dir, session)
        with YoutubeDL(_ydl_opts(pl_dir, audio_format, oauth, log_fn)) as ydl:
            log_fn(f"Downloading playlist: {url}")
            ydl.download([url])

    else:  # track or fallback
        owner = resource.get("user", {})
        owner_dir = out_dir / _safe_dirname(owner.get("permalink", "artist"))
        owner_dir.mkdir(parents=True, exist_ok=True)
        if owner.get("id"):
            download_artist_profile(_api_get(f"users/{owner['id']}", client_id, oauth), owner_dir, session)
        with YoutubeDL(_ydl_opts(owner_dir, audio_format, oauth, log_fn)) as ydl:
            log_fn(f"Downloading track: {url}")
            ydl.download([url])

    log_fn("Done.")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

BG        = "#0f0f0f"
BG2       = "#181818"
BG3       = "#222222"
ACCENT    = "#ff5500"        # SoundCloud orange
ACCENT_DIM= "#cc4400"
FG        = "#f0f0f0"
FG_DIM    = "#888888"
MONO      = "Menlo"
SANS      = "SF Pro Display"   # falls back gracefully on non-Mac

PAD = 18


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("sc_rip")
        self.configure(bg=BG)
        self.resizable(False, False)

        self._out_dir: Path | None = None
        self._running  = False
        self._log_queue: queue.Queue = queue.Queue()

        self._build()
        self._poll_log()

        # center on screen
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth()  // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    def _build(self):
        root = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        root.pack(fill="both", expand=True)

        # ---- header
        hdr = tk.Frame(root, bg=BG)
        hdr.pack(fill="x", pady=(0, PAD))

        tk.Label(hdr, text="◉", font=(SANS, 26), fg=ACCENT, bg=BG).pack(side="left")
        tk.Label(hdr, text=" sc_rip", font=(SANS, 22, "bold"), fg=FG, bg=BG).pack(side="left")
        tk.Label(hdr, text="soundcloud ripper", font=(SANS, 11), fg=FG_DIM, bg=BG).pack(
            side="left", padx=(8, 0), pady=(6, 0)
        )

        sep = tk.Frame(root, bg=ACCENT, height=1)
        sep.pack(fill="x", pady=(0, PAD))

        # ---- URL
        self._field("SoundCloud URL", root)
        self.url_var = tk.StringVar()
        self._entry(root, self.url_var, placeholder="https://soundcloud.com/artist/track")

        # ---- output dir
        self._field("Output Folder", root)
        dir_row = tk.Frame(root, bg=BG)
        dir_row.pack(fill="x", pady=(0, PAD))

        self.dir_label = tk.Label(
            dir_row, text="No folder selected", font=(MONO, 11),
            fg=FG_DIM, bg=BG2, anchor="w",
            padx=10, pady=8, relief="flat"
        )
        self.dir_label.pack(side="left", fill="x", expand=True)

        tk.Button(
            dir_row, text="Browse", font=(SANS, 11),
            fg=FG, bg=BG3, activebackground=ACCENT, activeforeground=FG,
            relief="flat", padx=12, cursor="hand2",
            command=self._pick_dir
        ).pack(side="left", padx=(8, 0))

        # ---- format + oauth row
        opts_row = tk.Frame(root, bg=BG)
        opts_row.pack(fill="x", pady=(0, PAD))

        # format
        fmt_col = tk.Frame(opts_row, bg=BG)
        fmt_col.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self._field("Format", fmt_col)
        self.fmt_var = tk.StringVar(value="mp3")
        fmt_frame = tk.Frame(fmt_col, bg=BG)
        fmt_frame.pack(fill="x")
        for fmt in ("mp3", "m4a", "opus"):
            tk.Radiobutton(
                fmt_frame, text=fmt.upper(), variable=self.fmt_var, value=fmt,
                font=(SANS, 11), fg=FG, bg=BG, selectcolor=BG,
                activebackground=BG, activeforeground=ACCENT,
                indicatoron=False, relief="flat",
                padx=10, pady=6, cursor="hand2",
                command=lambda: None,
            ).pack(side="left", padx=(0, 6))

        # oauth
        oauth_col = tk.Frame(opts_row, bg=BG)
        oauth_col.pack(side="left", fill="x", expand=True)
        self._field("OAuth Token (optional — for 256kbps)", oauth_col)
        self.oauth_var = tk.StringVar()
        self._entry(oauth_col, self.oauth_var, placeholder="OAuth x-111-222-333", show="•")

        # ---- download button
        self.dl_btn = tk.Button(
            root, text="DOWNLOAD",
            font=(SANS, 13, "bold"),
            fg=FG, bg=ACCENT, activebackground=ACCENT_DIM, activeforeground=FG,
            relief="flat", pady=12, cursor="hand2",
            command=self._start
        )
        self.dl_btn.pack(fill="x", pady=(4, PAD))

        # ---- log
        self._field("Log", root)
        log_frame = tk.Frame(root, bg=BG2, bd=0)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame, font=(MONO, 10), bg=BG2, fg="#aaaaaa",
            insertbackground=ACCENT, relief="flat",
            height=14, wrap="word", state="disabled",
            padx=10, pady=8,
        )
        self.log_text.pack(side="left", fill="both", expand=True)

        sb = tk.Scrollbar(log_frame, command=self.log_text.yview, bg=BG3, troughcolor=BG2)
        sb.pack(side="right", fill="y")
        self.log_text["yscrollcommand"] = sb.set

        # tag for orange highlights
        self.log_text.tag_configure("accent", foreground=ACCENT)
        self.log_text.tag_configure("err",    foreground="#ff4444")

    def _field(self, label, parent):
        tk.Label(parent, text=label.upper(), font=(SANS, 9),
                 fg=FG_DIM, bg=BG, anchor="w"
        ).pack(fill="x", pady=(0, 4))

    def _entry(self, parent, var, placeholder="", show=""):
        e = tk.Entry(
            parent, textvariable=var,
            font=(MONO, 11), fg=FG_DIM, bg=BG2,
            insertbackground=FG, relief="flat",
            show=show if show else "",
        )
        e.pack(fill="x", pady=(0, PAD), ipady=8, ipadx=10)

        if placeholder:
            e.insert(0, placeholder)
            e.config(fg=FG_DIM)

            def on_focus_in(ev):
                if e.get() == placeholder:
                    e.delete(0, "end")
                    e.config(fg=FG)

            def on_focus_out(ev):
                if not e.get():
                    e.insert(0, placeholder)
                    e.config(fg=FG_DIM)

            e.bind("<FocusIn>",  on_focus_in)
            e.bind("<FocusOut>", on_focus_out)

        return e

    # ------------------------------------------------------------------
    def _pick_dir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self._out_dir = Path(d)
            # truncate long paths for display
            display = str(self._out_dir)
            if len(display) > 55:
                display = "..." + display[-52:]
            self.dir_label.config(text=display, fg=FG)

    def _log(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_text.config(state="normal")
                tag = "err" if msg.startswith("[err") else ("accent" if msg.startswith("[+]") or msg == "Done." else "")
                self.log_text.insert("end", msg + "\n", tag)
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    def _start(self):
        if self._running:
            return

        url = self.url_var.get().strip()
        placeholder_url = "https://soundcloud.com/artist/track"
        if not url or url == placeholder_url:
            messagebox.showwarning("Missing URL", "Please enter a SoundCloud URL.")
            return
        if not url.startswith("https://soundcloud.com"):
            messagebox.showwarning("Bad URL", "URL must start with https://soundcloud.com")
            return
        if not self._out_dir:
            messagebox.showwarning("No folder", "Please select an output folder.")
            return

        oauth_raw = self.oauth_var.get().strip()
        placeholder_oauth = "OAuth x-111-222-333"
        oauth = oauth_raw if oauth_raw and oauth_raw != placeholder_oauth else None

        fmt = self.fmt_var.get()

        self._running = True
        self.dl_btn.config(text="DOWNLOADING...", bg=ACCENT_DIM, state="disabled")

        def worker():
            try:
                rip(url, self._out_dir, fmt, oauth, self._log)
            except Exception as e:
                self._log(f"[err] {e}")
            finally:
                self._running = False
                self.after(0, lambda: self.dl_btn.config(
                    text="DOWNLOAD", bg=ACCENT, state="normal"
                ))

        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.mainloop()