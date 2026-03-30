# sc_rip

SoundCloud ripper with full metadata, embedded artwork, and artist profile dumps. Comes in two flavors — a native GUI app and a CLI script.

---

## Files

| File | What it is |
|------|------------|
| `sc_rip_gui.py` | GUI app — folder picker, format selector, log window. Start here. |
| `sc_rip.py` | CLI version for scripting or batch use. |

---

## Install

```bash
pip install yt-dlp mutagen requests Pillow
```

You also need FFmpeg on your PATH for audio extraction and thumbnail embedding.

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
apt install ffmpeg

# Windows
# download from https://ffmpeg.org/download.html and add to PATH
```

`tkinter` (used by the GUI) ships with Python — no extra install needed.

---

## GUI

```bash
python sc_rip_gui.py
```

A window opens. Paste a SoundCloud URL, pick an output folder, choose a format, optionally add your OAuth token, hit **DOWNLOAD**. The log box streams output in real time.

---

## CLI

```bash
python sc_rip.py <url> [--out <dir>] [--format mp3|m4a|opus] [--oauth <token>]
```

```bash
# single track
python sc_rip.py https://soundcloud.com/artist/trackname

# full discography
python sc_rip.py https://soundcloud.com/artist

# playlist / set
python sc_rip.py https://soundcloud.com/artist/sets/setname

# specify output dir and format
python sc_rip.py https://soundcloud.com/artist --out ~/Music/sc --format m4a

# with OAuth token for 256kbps
python sc_rip.py https://soundcloud.com/artist --oauth "OAuth x-111-222-333"
```

---

## What gets saved

Every run saves the audio file with embedded cover art and ID3 tags, plus a set of sidecar files:

```
sc_downloads/
└── artist-slug/
    ├── artist_profile.json         # follower count, bio, social links, city, verified status
    ├── avatar_original.jpg         # full-res avatar (not the 100x100 thumbnail)
    ├── banner.jpg                  # profile banner if the artist has one
    ├── Track Title [id].mp3        # audio with embedded art + tags
    └── Track Title [id].info.json  # raw SC metadata: BPM, ISRC, waveform URL, play/like counts, genre
```

For playlists there's an extra level:

```
sc_downloads/
└── artist-slug/
    ├── artist_profile.json
    ├── avatar_original.jpg
    └── set-slug/
        ├── playlist_metadata.json
        ├── artwork.jpg
        └── Track Title [id].mp3
```

---

## OAuth token — 128kbps vs 256kbps

Without a token you get 128kbps. With one you get 256kbps AAC.

1. Go to soundcloud.com and log in
2. Open DevTools (`F12`) and go to the **Network** tab
3. Filter requests by `session`
4. Click the request and look at the `Authorization` header value
5. It looks like `OAuth x-123456-789012345-abcdef` — copy the whole thing
6. Paste it into the OAuth field in the GUI, or pass it to `--oauth` in the CLI

The token is tied to your session and will eventually expire. If downloads start failing, refresh it.

---

## Formats

| Option | Container | Notes |
|--------|-----------|-------|
| `mp3` (default) | MP3 | universally compatible, VBR best quality |
| `m4a` | AAC/M4A | better quality-per-bit, works natively in Apple ecosystem |
| `opus` | Opus/WebM | best compression ratio, not supported everywhere |

---

## How the client_id works

SoundCloud closed public API registration years ago. Both scripts scrape the SoundCloud homepage at runtime, collect all hashed JS bundle URLs (`a-v2.sndcdn.com/assets/*.js`), and regex-search them for the 32-character `client_id` embedded in the frontend code. This is the same approach yt-dlp uses internally. It runs fresh on every invocation so a rotated key never breaks anything.

---

## Notes

- When given a user URL the script pages through tracks 50 at a time with a 300ms delay between requests
- The `.info.json` sidecar includes everything SC returns: waveform data, play/like/repost counts, BPM if set, genre tags, and publisher metadata (ISRC, label, p-line) when the artist has filled them in
- The GUI runs downloads on a background thread so the window stays responsive; clicking Download while one is running does nothing
- The OAuth field in the GUI masks input with bullets so the token isn't visible if you're screen sharing