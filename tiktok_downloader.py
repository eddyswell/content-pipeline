"""
tiktok_downloader.py — Download a TikTok slideshow via yt-dlp and store in SQLite.

Usage:
    python tiktok_downloader.py https://www.tiktok.com/@user/photo/1234567890
    python tiktok_downloader.py urls.txt          # one URL per line
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config

DB_PATH   = Path(config.TIKTOK_DB_PATH)
OUT_ROOT  = Path(config.TIKTOK_VIRAL_SLIDES_DIR)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


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


# ── Download ──────────────────────────────────────────────────────────────────

def _extract_post_id(url: str) -> str:
    """Best-effort post ID from URL or yt-dlp metadata."""
    parts = url.rstrip("/").split("/")
    for p in reversed(parts):
        p = p.split("?")[0]
        if p.isdigit() and len(p) > 5:
            return p
    return parts[-1].split("?")[0]


COOKIES_FILE   = Path("data/tiktok_cookies.txt")   # manual export fallback
BROWSER_PREF   = Path("data/browser_pref.txt")     # stores chosen browser name

# Browsers yt-dlp can read from directly
SUPPORTED_BROWSERS = ["brave", "chrome", "firefox", "safari", "edge", "chromium", "opera"]


def get_browser_pref() -> str | None:
    """Return the saved browser preference, or None if not set."""
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


def _build_cmd(url: str, out_dir: Path) -> list[str]:
    """
    Build the yt-dlp command with the best available auth method:
      1. Cookies file (data/tiktok_cookies.txt) — works anywhere incl. remote
      2. Browser extraction (--cookies-from-browser brave/chrome/…) — local only
      3. No auth — public posts only
    """
    cmd = [
        "yt-dlp",
        "--write-info-json",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "-o", str(out_dir / "%(id)s_%(playlist_index)s.%(ext)s"),
    ]
    if COOKIES_FILE.exists():
        # Explicit file takes priority (works in remote container too)
        cmd += ["--cookies", str(COOKIES_FILE)]
    elif (browser := get_browser_pref()):
        # Pull cookies straight from the local browser — no export needed
        cmd += ["--cookies-from-browser", browser]
    return cmd + [url]


def download(url: str, force: bool = False) -> dict | None:
    """
    Download a TikTok slideshow URL. Returns a record dict or None on failure.
    Idempotent — if already downloaded successfully, returns existing record.
    Set force=True to re-download even if already in DB.
    Returns a dict with an extra '_error' key on failure so callers can show the reason.
    """
    init_db()
    url = url.strip()
    post_id = _extract_post_id(url)

    if not force:
        existing = get_post(post_id)
        if existing and existing["status"] == "success":
            print(f"  ↩  Already downloaded ({post_id}), skipping.")
            existing["image_paths"] = json.loads(existing["image_paths"])
            return existing

    out_dir = OUT_ROOT / post_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = _build_cmd(url, out_dir)
    print(f"  ↓  Downloading {url}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()

        # Surface the real yt-dlp error for the UI
        if result.returncode != 0 and not any(out_dir.glob("*")):
            err_lines = [l for l in (stderr or stdout).splitlines()
                         if "ERROR" in l or "error" in l.lower()]
            err_msg = err_lines[0] if err_lines else (stderr or stdout or "unknown error")[:300]
            print(f"  ✗  {err_msg}")
            return {"_error": err_msg}

    except subprocess.TimeoutExpired:
        return {"_error": "yt-dlp timed out after 120 s"}

    # Find info JSON
    info_files = list(out_dir.glob("*.info.json"))
    info: dict = {}
    if info_files:
        try:
            info = json.loads(info_files[0].read_text(encoding="utf-8"))
        except Exception:
            pass

    # Collect images (exclude thumbnails named *_NA.* or *.info.json)
    images = sorted(
        f for f in out_dir.iterdir()
        if f.suffix.lower() in IMAGE_EXT
        and "_NA." not in f.name
        and not f.name.endswith(".info.json")
    )

    if not images:
        # Fallback: yt-dlp may have written the slideshow differently
        images = sorted(f for f in out_dir.iterdir() if f.suffix.lower() in IMAGE_EXT)

    image_paths = [str(p.relative_to(Path.cwd())) for p in images]

    real_post_id = info.get("id", post_id)
    record = {
        "post_id":    real_post_id,
        "url":        url,
        "caption":    (info.get("description") or info.get("title") or ""),
        "author":     info.get("uploader") or info.get("creator") or "",
        "like_count": info.get("like_count") or 0,
        "slide_count": len(image_paths),
        "image_paths": image_paths,
        "metadata":   info,
        "status":     "success" if image_paths else "no_images",
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
