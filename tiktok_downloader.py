"""
tiktok_downloader.py — Download TikTok slideshows and store in SQLite.

/photo/ URLs: scraped directly from page HTML (no external tools, no auth needed)
/video/ URLs: yt-dlp

Usage:
    python tiktok_downloader.py https://www.tiktok.com/@user/photo/1234567890
    python tiktok_downloader.py urls.txt          # one URL per line
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

import config

DB_PATH   = Path(config.TIKTOK_DB_PATH)
OUT_ROOT  = Path(config.TIKTOK_VIRAL_SLIDES_DIR)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# ── Database ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id       TEXT    UNIQUE,
                url           TEXT,
                caption       TEXT,
                author        TEXT,
                like_count    INTEGER,
                download_date TEXT,
                slide_count   INTEGER,
                image_paths   TEXT,   -- JSON array of relative paths
                metadata_json TEXT,
                status        TEXT
            );

            CREATE TABLE IF NOT EXISTS hooks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id          TEXT REFERENCES posts(post_id),
                hook_text        TEXT,
                why_it_works     TEXT,
                hook_structure   TEXT,
                hook_type        TEXT,   -- curiosity | pain_point | surprise | relatability | fomo
                variations       TEXT,   -- JSON array of 5 hook variations
                pinterest_queries TEXT,  -- JSON array of search terms
                niche            TEXT,
                analyzed_date    TEXT
            );
        """)


def get_post(post_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        return dict(row) if row else None


def save_post(record: dict):
    with _get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO posts
            (post_id, url, caption, author, like_count, download_date,
             slide_count, image_paths, metadata_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record["post_id"], record["url"], record["caption"], record["author"],
            record.get("like_count", 0), datetime.now().isoformat(),
            record["slide_count"], json.dumps(record["image_paths"]),
            json.dumps(record.get("metadata", {})), record["status"],
        ))


def save_hooks(post_id: str, analysis: dict, niche: str = "personal finance"):
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO hooks
            (post_id, hook_text, why_it_works, hook_structure, hook_type,
             variations, pinterest_queries, niche, analyzed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post_id,
            analysis.get("hook", ""),
            analysis.get("why_it_works", ""),
            analysis.get("hook_structure", ""),
            analysis.get("hook_type", ""),
            json.dumps(analysis.get("variations", [])),
            json.dumps(analysis.get("pinterest_queries", [])),
            niche,
            datetime.now().isoformat(),
        ))


# ── URL helpers ───────────────────────────────────────────────────────────────

_SHORT_TIKTOK_DOMAINS = {"vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com"}


def _resolve_url(url: str) -> str:
    """
    Follow redirects for short TikTok URLs so routing logic can inspect the real path.
    vm.tiktok.com/XYZ  →  https://www.tiktok.com/@user/photo/123?…
    Falls back silently to the original URL on any network error.
    """
    import urllib.request
    from urllib.parse import urlparse
    if urlparse(url.strip()).netloc not in _SHORT_TIKTOK_DOMAINS:
        return url
    try:
        req = urllib.request.Request(url.strip(), headers={"User-Agent": _MOBILE_UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.url
    except Exception:
        return url


def _clean_url(url: str) -> str:
    """Strip tracking params: https://tiktok.com/@u/photo/123?_r=1  →  …/photo/123"""
    from urllib.parse import urlparse, urlunparse
    p = urlparse(url.strip())
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _extract_post_id(url: str) -> str:
    parts = url.rstrip("/").split("/")
    for p in reversed(parts):
        p = p.split("?")[0]
        if p.isdigit() and len(p) > 5:
            return p
    return parts[-1].split("?")[0]


# ── Cookie / browser auth ─────────────────────────────────────────────────────

COOKIES_FILE   = Path("data/tiktok_cookies.txt")
BROWSER_PREF   = Path("data/browser_pref.txt")
SUPPORTED_BROWSERS = ["brave", "chrome", "firefox", "safari", "edge", "chromium", "opera"]


def get_browser_pref() -> str | None:
    if BROWSER_PREF.exists():
        v = BROWSER_PREF.read_text().strip().lower()
        return v if v in SUPPORTED_BROWSERS else None
    return None


def set_browser_pref(browser: str | None) -> None:
    BROWSER_PREF.parent.mkdir(parents=True, exist_ok=True)
    if browser:
        BROWSER_PREF.write_text(browser.lower())
    elif BROWSER_PREF.exists():
        BROWSER_PREF.unlink()


def _requests_session() -> requests.Session:
    """Build a requests session, loading cookies from file if available."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": _MOBILE_UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/",
    })
    if COOKIES_FILE.exists():
        import http.cookiejar
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(str(COOKIES_FILE), ignore_discard=True, ignore_expires=True)
            session.cookies = jar  # type: ignore[assignment]
        except Exception:
            pass
    return session


# ── /photo/ downloader (pure requests, no external tools) ────────────────────

def _scrape_photo_slides(url: str, out_dir: Path) -> dict:
    """
    Download a TikTok slideshow by parsing the page HTML directly.
    No gallery-dl, no yt-dlp. Works for public posts without login.

    Returns {"info": {...}, "saved": [Path, ...]} or {"_error": msg}.
    """
    session = _requests_session()

    try:
        resp = session.get(url, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return {"_error": f"Could not fetch TikTok page: {exc}"}

    # Log the final URL (useful when a short link redirected)
    if resp.url != url:
        print(f"  ↪  Resolved to {resp.url.split('?')[0]}")

    # TikTok embeds all post data in a <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> tag
    match = re.search(
        r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        resp.text, re.DOTALL,
    )
    if not match:
        return {"_error": "TikTok page structure not recognised — page may have changed"}

    try:
        data  = json.loads(match.group(1))
        scope = data["__DEFAULT_SCOPE__"]
        # Try both known key names (TikTok has changed this before)
        detail = (
            scope.get("webapp.reflow.video.detail")
            or scope.get("webapp.video-detail")
            or {}
        )
        item = detail.get("itemInfo", {}).get("itemStruct", {})
    except Exception as exc:
        return {"_error": f"Failed to parse TikTok data: {exc}"}

    if not item:
        return {"_error": "Post data missing — it may be private or geo-blocked"}

    images_data = item.get("imagePost", {}).get("images", [])
    if not images_data:
        return {"_error": "No slideshow images found — this looks like a video, not a photo post"}

    post_id = item.get("id", _extract_post_id(url))
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for i, img_data in enumerate(images_data, 1):
        url_list = img_data.get("imageURL", {}).get("urlList", [])
        if not url_list:
            continue
        img_url = url_list[0]
        try:
            ir = session.get(img_url, timeout=30)
            ir.raise_for_status()
            ct  = ir.headers.get("content-type", "image/jpeg")
            ext = "jpg" if "jpeg" in ct else ct.split("/")[-1].split(";")[0].strip() or "jpg"
            fpath = out_dir / f"{post_id}_{i:02d}.{ext}"
            fpath.write_bytes(ir.content)
            saved.append(fpath)
        except Exception as exc:
            print(f"  ⚠  Slide {i} failed: {exc}")

    info = {
        "id":          post_id,
        "description": item.get("desc", ""),
        "title":       item.get("desc", ""),
        "uploader":    item.get("author", {}).get("uniqueId", ""),
        "like_count":  item.get("stats", {}).get("diggCount", 0),
    }
    return {"info": info, "saved": saved}


# ── /video/ downloader (yt-dlp) ───────────────────────────────────────────────

def _build_ytdlp_cmd(url: str, out_dir: Path) -> list[str]:
    cmd = [
        "yt-dlp",
        "--write-info-json",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent", _MOBILE_UA,
        "-o", str(out_dir / "%(id)s_%(playlist_index)s.%(ext)s"),
    ]
    if COOKIES_FILE.exists():
        cmd += ["--cookies", str(COOKIES_FILE)]
    elif (browser := get_browser_pref()):
        cmd += ["--cookies-from-browser", browser]
    return cmd + [url]


# ── Unified download ──────────────────────────────────────────────────────────

def download(url: str, force: bool = False) -> dict:
    """
    Download a TikTok URL and store in SQLite.
    - /photo/ URLs or short links → pure Python HTML scraper (requests follows redirects)
    - /video/ URLs → yt-dlp
    Idempotent: skips if already downloaded successfully (unless force=True).
    Always returns a dict; failure has a '_error' key.
    """
    init_db()
    url = _resolve_url(url)   # best-effort expand of vm.tiktok.com short links
    url = _clean_url(url)     # strip tracking params
    post_id = _extract_post_id(url)

    if not force:
        existing = get_post(post_id)
        if existing and existing["status"] == "success":
            print(f"  ↩  Already downloaded ({post_id}), skipping.")
            existing["image_paths"] = json.loads(existing["image_paths"])
            return existing

    out_dir = OUT_ROOT / post_id
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  ↓  Downloading {url}")

    info: dict = {}
    image_paths: list[str] = []

    # Route: explicit /photo/ URL OR short link (requests follows the redirect itself)
    from urllib.parse import urlparse as _up
    _is_short = _up(url).netloc in _SHORT_TIKTOK_DOMAINS
    if "/photo/" in url or _is_short:
        # ── Pure Python scraper (TikTok slideshow) ───────────────────────────
        result = _scrape_photo_slides(url, out_dir)
        if "_error" in result:
            print(f"  ✗  {result['_error']}")
            return result
        info = result.get("info", {})
        saved = result.get("saved", [])
        _cwd = Path.cwd().resolve()
        image_paths = [str(p.resolve().relative_to(_cwd)) for p in saved]

    else:
        # ── yt-dlp (TikTok video) ────────────────────────────────────────────
        cmd = _build_ytdlp_cmd(url, out_dir)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            stderr = proc.stderr.strip()
            stdout = proc.stdout.strip()
            if proc.returncode != 0 and not any(out_dir.glob("*")):
                err_lines = [l for l in (stderr or stdout).splitlines()
                             if "ERROR" in l or "error" in l.lower()]
                err_msg = err_lines[0] if err_lines else (stderr or stdout or "unknown error")[:300]
                print(f"  ✗  {err_msg}")
                return {"_error": err_msg}
        except subprocess.TimeoutExpired:
            return {"_error": "yt-dlp timed out after 120 s"}

        info_files = list(out_dir.glob("*.info.json"))
        if info_files:
            try:
                info = json.loads(info_files[0].read_text(encoding="utf-8"))
            except Exception:
                pass

        images = sorted(
            f for f in out_dir.iterdir()
            if f.suffix.lower() in IMAGE_EXT and "_NA." not in f.name
        )
        _cwd = Path.cwd().resolve()
        image_paths = [str(p.resolve().relative_to(_cwd)) for p in images]

    real_post_id = info.get("id", post_id)
    record = {
        "post_id":     real_post_id,
        "url":         url,
        "caption":     info.get("description") or info.get("title") or "",
        "author":      info.get("uploader") or info.get("creator") or "",
        "like_count":  info.get("like_count") or info.get("diggCount") or 0,
        "slide_count": len(image_paths),
        "image_paths": image_paths,
        "metadata":    info,
        "status":      "success" if image_paths else "no_images",
    }
    save_post(record)

    if image_paths:
        print(f"  ✓  {len(image_paths)} slide(s) saved → {out_dir}/")
    else:
        print(f"  ⚠  Downloaded but found no images in {out_dir}/")

    return record


def download_many(urls: list[str]) -> list[dict]:
    results = []
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}]")
        rec = download(url)
        if rec:
            results.append(rec)
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tiktok_downloader.py <url>  OR  <urls.txt>")
        sys.exit(1)

    arg = sys.argv[1]
    if arg.endswith(".txt") and Path(arg).exists():
        urls = [l.strip() for l in Path(arg).read_text().splitlines()
                if l.strip() and not l.startswith("#")]
        print(f"Processing {len(urls)} URLs from {arg}...\n")
        download_many(urls)
    else:
        download(arg)
