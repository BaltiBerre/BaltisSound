# sc_rip

A Python CLI for ripping SoundCloud tracks with full metadata, embedded artwork, and artist profile dumps.

## What it does

- Downloads tracks, playlists, or an artist's full discography
- Embeds cover art and ID3 tags directly into the audio file
- Saves a `metadata.json` per track (play counts, genre, BPM, waveform URL, publisher info, etc.)
- Saves an `artist_profile.json` with follower count, bio, social links, city, verified status
- Downloads the artist avatar at original resolution and the banner image if present
- Supports 256kbps AAC streams if you supply your OAuth token (vs 128kbps without it)

## Install

```bash
pip install yt-dlp mutagen requests Pillow
```

You also need FFmpeg on your PATH for audio extraction and thumbnail embedding.
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `apt install ffmpeg`
- Windows: download from https://ffmpeg.org/download.html and add to PATH

## Usage

```bash
python sc_rip.py <url> [--out <dir>] [--format mp3|m4a|opus] [--oauth <token>]
```

### Examples

```bash
# single track
python sc_rip.py https://soundcloud.com/artist/trackname

# full discography (all uploads, no reposts)
python sc_rip.py https://soundcloud.com/artist

# playlist / set
python sc_rip.py https://soundcloud.com/artist/sets/setname

# specify output directory and format
python sc_rip.py https://soundcloud.com/artist --out ~/Music/sc --format m4a

# with OAuth token for 256kbps streams
python sc_rip.py https://soundcloud.com/artist --oauth "OAuth x-111-222-333"
```

## Getting your OAuth token (optional but recommended)

Without it you get 128kbps. With it you get 256kbps AAC.

1. Go to soundcloud.com and log in
2. Open DevTools (F12) and go to the Network tab
3. Filter by `session`
4. Look at the `Authorization` request header value — it looks like `OAuth x-123456-789012345-abcdef`
5. Pass that string to `--oauth`

## Output structure

```
sc_downloads/
└── artist-slug/
    ├── artist_profile.json      # full user object from SC v2 API
    ├── avatar_original.jpg      # full-res avatar
    ├── banner.jpg               # profile banner if present
    └── Track Title [id].mp3     # audio with embedded art + ID3 tags
    └── Track Title [id].info.json  # raw metadata dump per track
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

## How the client_id works

SoundCloud closed public API registration years ago. The script scrapes the SoundCloud homepage at runtime, collects all hashed JS bundle URLs, and regex-searches them for the 32-character `client_id` string embedded in the frontend code. This is the same approach yt-dlp uses internally and gets refreshed on every run, so you're never stuck on a stale key.

## Formats

| Flag | Container | Notes |
|------|-----------|-------|
| `mp3` (default) | MP3 | universally compatible, VBR best quality |
| `m4a` | AAC/M4A | better quality-per-bit than mp3, works in Apple ecosystem |
| `opus` | Opus/WebM | best compression, not supported everywhere |

## Notes

- The script pages through all tracks when given a user URL (50 per request), so large discographies take a while
- It adds a 300ms delay between pagination requests to avoid hammering the API
- Per-track `.info.json` files include waveform data, play/like/repost counts, BPM if set, genre tags, and publisher metadata (ISRC, label, etc.) when the artist has filled them in