#!/usr/bin/env python3
"""
sc_rip.py  --  SoundCloud ripper with full metadata + artist profile
requires: yt-dlp, mutagen, requests, Pillow
    pip install yt-dlp mutagen requests Pillow

Usage:
    python sc_rip.py <soundcloud_url> [--out <dir>] [--format mp3|m4a|opus] [--oauth <token>]

Examples:
    python sc_rip.py https://soundcloud.com/artist-slug/track-slug
    python sc_rip.py https://soundcloud.com/artist-slug              # full discography
    python sc_rip.py https://soundcloud.com/artist-slug/sets/mix     # playlist
    python sc_rip.py https://soundcloud.com/artist-slug --oauth "OAuth x-123456-..."
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# client_id scraping (same approach yt-dlp uses internally)
# ---------------------------------------------------------------------------

SC_HOME = "https://soundcloud.com"
SC_API  = "https://api-v2.soundcloud.com"

_client_id_cache: str | None = None

def _scrape_client_id() -> str:
    """Scrape the SoundCloud frontend JS bundles to extract a valid client_id."""
    global _client_id_cache
    if _client_id_cache:
        return _client_id_cache

    print("[*] Scraping client_id from SoundCloud JS bundles...")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    html = requests.get(SC_HOME, headers=headers, timeout=15).text

    # Grab every <script src="..."> that looks like a hashed asset bundle
    script_urls = re.findall(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)

    # yt-dlp reverses the list -- later bundles are more likely to contain the id
    for url in reversed(script_urls):
        try:
            js = requests.get(url, headers=headers, timeout=10).text
            m = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', js)
            if m:
                _client_id_cache = m.group(1)
                print(f"[+] client_id: {_client_id_cache[:8]}...")
                return _client_id_cache
        except Exception:
            continue

    raise RuntimeError("Could not extract client_id from SoundCloud. Site may have changed.")


# ---------------------------------------------------------------------------
# SoundCloud v2 API helpers
# ---------------------------------------------------------------------------

def _api_get(path: str, client_id: str, oauth: str | None = None, **params) -> dict:
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


def resolve_url(url: str, client_id: str, oauth: str | None = None) -> dict:
    """Resolve any SoundCloud permalink to its resource dict."""
    return _api_get("resolve", client_id, oauth, url=url)


def fetch_user_tracks(user_id: int, client_id: str, oauth: str | None = None) -> list[dict]:
    """Page through /users/{id}/tracks until exhausted."""
    tracks = []
    next_href = None
    url = f"users/{user_id}/tracks"
    params: dict = {"limit": 50, "linked_partitioning": 1}

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
            data = _api_get(url, client_id, oauth, **params)

        tracks.extend(data.get("collection", []))
        next_href = data.get("next_href")
        if not next_href:
            break
        time.sleep(0.3)  # be polite

    return tracks


# ---------------------------------------------------------------------------
# Artist profile download
# ---------------------------------------------------------------------------

def download_artist_profile(user: dict, out_dir: Path, session: requests.Session) -> None:
    profile = {
        "id":            user.get("id"),
        "permalink":     user.get("permalink"),
        "username":      user.get("username"),
        "full_name":     user.get("full_name"),
        "description":   user.get("description"),
        "city":          user.get("city"),
        "country_code":  user.get("country_code"),
        "followers_count":  user.get("followers_count"),
        "followings_count": user.get("followings_count"),
        "track_count":   user.get("track_count"),
        "likes_count":   user.get("likes_count"),
        "reposts_count": user.get("reposts_count"),
        "verified":      user.get("verified"),
        "website":       user.get("website"),
        "website_title": user.get("website_title"),
        "permalink_url": user.get("permalink_url"),
        "avatar_url":    user.get("avatar_url"),
        # visuals is a banner image object
        "visuals":       user.get("visuals"),
    }

    profile_path = out_dir / "artist_profile.json"
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    print(f"[+] Artist profile saved -> {profile_path}")

    # Download full-resolution avatar
    # SoundCloud serves thumbnails at 200x200; replace size suffix to get original
    avatar_url = user.get("avatar_url")
    if avatar_url:
        # "large" = 100x100, "t500x500" = 500x500, "original" = original upload
        full_avatar = re.sub(r"-large\.jpg", "-original.jpg", avatar_url)
        _download_image(full_avatar, out_dir / "avatar_original.jpg", session, fallback=avatar_url)

    # Download banner / visuals if present
    visuals = user.get("visuals", {}) or {}
    for vis in visuals.get("visuals", []):
        banner_url = vis.get("visual_url")
        if banner_url:
            _download_image(banner_url, out_dir / "banner.jpg", session)
            break


def _download_image(url: str, dest: Path, session: requests.Session, fallback: str | None = None) -> bool:
    for attempt_url in ([url, fallback] if fallback and fallback != url else [url]):
        if not attempt_url:
            continue
        try:
            r = session.get(attempt_url, timeout=15)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                print(f"[+] Image saved -> {dest.name} ({len(r.content) // 1024} KB)")
                return True
        except Exception as e:
            print(f"[!] Image download failed ({attempt_url}): {e}")
    return False


# ---------------------------------------------------------------------------
# yt-dlp download with full metadata + embedded art
# ---------------------------------------------------------------------------

def _ydl_opts(out_dir: Path, audio_format: str, oauth: str | None) -> dict:
    opts: dict = {
        "format":          "bestaudio/best",
        "outtmpl":         str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "writethumbnail":  True,          # saves cover art as separate file too
        "embedthumbnail":  True,
        "addmetadata":     True,
        "postprocessors": [
            {
                "key":            "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                # 0 = best quality (VBR for mp3, native for m4a/opus)
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
        # Write info JSON alongside each track
        "writeinfojson": True,
        "quiet":         False,
        "no_warnings":   False,
    }

    if oauth:
        # Pass OAuth token so SoundCloud returns 256kbps HLS instead of 128kbps
        opts["http_headers"] = {"Authorization": oauth}

    return opts


def download_tracks(urls: list[str], out_dir: Path, audio_format: str, oauth: str | None) -> None:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        sys.exit("[!] yt-dlp not found. Run: pip install yt-dlp")

    opts = _ydl_opts(out_dir, audio_format, oauth)
    with YoutubeDL(opts) as ydl:
        for url in urls:
            print(f"\n[yt-dlp] downloading: {url}")
            try:
                ydl.download([url])
            except Exception as e:
                print(f"[!] Failed: {url}\n    {e}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def rip(url: str, out_dir: Path, audio_format: str, oauth: str | None) -> None:
    client_id = _scrape_client_id()
    session   = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    print(f"\n[*] Resolving: {url}")
    resource = resolve_url(url, client_id, oauth)
    kind      = resource.get("kind")  # "track", "playlist", "user"

    print(f"[*] Resource kind: {kind}")

    if kind == "user":
        user      = resource
        artist_dir = out_dir / _safe_dirname(user.get("permalink", "artist"))
        artist_dir.mkdir(parents=True, exist_ok=True)

        download_artist_profile(user, artist_dir, session)

        print(f"[*] Fetching track list for {user.get('username')}...")
        tracks = fetch_user_tracks(user["id"], client_id, oauth)
        print(f"[*] Found {len(tracks)} tracks")

        track_urls = [t["permalink_url"] for t in tracks if t.get("permalink_url")]
        download_tracks(track_urls, artist_dir, audio_format, oauth)

    elif kind == "playlist":
        playlist   = resource
        owner      = playlist.get("user", {})
        owner_dir  = out_dir / _safe_dirname(owner.get("permalink", "artist"))
        pl_dir     = owner_dir / _safe_dirname(playlist.get("permalink", "playlist"))
        pl_dir.mkdir(parents=True, exist_ok=True)

        # Save playlist metadata
        pl_meta = {
            "id":          playlist.get("id"),
            "title":       playlist.get("title"),
            "description": playlist.get("description"),
            "track_count": playlist.get("track_count"),
            "genre":       playlist.get("genre"),
            "artwork_url": playlist.get("artwork_url"),
            "permalink_url": playlist.get("permalink_url"),
            "created_at":  playlist.get("created_at"),
            "owner":       {k: owner.get(k) for k in ("id", "username", "permalink", "avatar_url")},
        }
        (pl_dir / "playlist_metadata.json").write_text(
            json.dumps(pl_meta, indent=2, ensure_ascii=False)
        )

        # Download playlist artwork if present
        art_url = playlist.get("artwork_url")
        if art_url:
            full_art = re.sub(r"-large\.jpg", "-t500x500.jpg", art_url)
            _download_image(full_art, pl_dir / "artwork.jpg", session, fallback=art_url)

        # Also pull artist profile
        if owner.get("id"):
            owner_full = _api_get(f"users/{owner['id']}", client_id, oauth)
            download_artist_profile(owner_full, owner_dir, session)

        download_tracks([url], pl_dir, audio_format, oauth)

    elif kind == "track":
        track     = resource
        owner     = track.get("user", {})
        owner_dir = out_dir / _safe_dirname(owner.get("permalink", "artist"))
        owner_dir.mkdir(parents=True, exist_ok=True)

        # Save artist profile once
        if owner.get("id"):
            owner_full = _api_get(f"users/{owner['id']}", client_id, oauth)
            download_artist_profile(owner_full, owner_dir, session)

        download_tracks([url], owner_dir, audio_format, oauth)

    else:
        print(f"[!] Unrecognised resource kind '{kind}'. Attempting direct download anyway.")
        out_dir.mkdir(parents=True, exist_ok=True)
        download_tracks([url], out_dir, audio_format, oauth)


def _safe_dirname(name: str) -> str:
    """Strip characters that are illegal in directory names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SoundCloud ripper: audio + full metadata + artist profile"
    )
    parser.add_argument("url",    help="SoundCloud URL (track, playlist, or user page)")
    parser.add_argument("--out",  default="./sc_downloads", help="Output directory (default: ./sc_downloads)")
    parser.add_argument("--format", default="mp3", choices=["mp3", "m4a", "opus"],
                        help="Audio format (default: mp3)")
    parser.add_argument("--oauth", default=None,
                        help='OAuth token for 256kbps. Format: "OAuth x-111-222-333"\n'
                             'Get it: open soundcloud.com, DevTools -> Network, '
                             'filter by "session", find Authorization header value.')
    args = parser.parse_args()

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[sc_rip] output dir : {out_dir}")
    print(f"[sc_rip] audio format: {args.format}")
    print(f"[sc_rip] oauth       : {'yes' if args.oauth else 'no (128kbps)'}\n")

    rip(args.url, out_dir, args.format, args.oauth)
    print("\n[sc_rip] done.")


if __name__ == "__main__":
    main()