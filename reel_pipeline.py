"""
reel_pipeline.py — Instagram Reel → AI Creator Reel pipeline.

Flow:
  1. download_reel(url, out_dir)                    → Path  (video file)
  2. extract_first_frame(video_path, out_path)       → Path  (PNG frame)
  3. [caller] fill_prompt_with_claude(frame, ...)    → str   (JSON prompt)
  4. [caller] generate_base(collage, frame, prompt)  → Path  (AI image)
  5. generate_kling_video(image, video, out_path)    → Path  (output video)

Steps 3+4 use the existing generate_images module unchanged.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import ssl
import subprocess
from pathlib import Path

import aiohttp
import certifi
from dotenv import load_dotenv

import wavespeed_utils as ws

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

# WaveSpeed model path for Kling image-to-video.
# Change this if WaveSpeed updates the path.
KLING_MODEL = "kling-ai/kling-1-6/standard/image-to-video"

OUTPUT_DIR = Path("output/reel_pipeline")

KLING_MOTION_PROMPT = (
    "woman speaking naturally to camera, relaxed upper body movement, "
    "natural hand gestures, confident and expressive, realistic motion"
)


# ── Part 1 — Download reel ────────────────────────────────────────────────────

def download_reel(url: str, out_dir: Path) -> Path:
    """
    Download an Instagram reel using yt-dlp.
    Returns the path to the downloaded MP4 file.
    Raises RuntimeError with a clear message on any failure.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp not installed. Run: pip install yt-dlp"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "reel.%(ext)s")

    # Clean up any previous reel file in this dir
    for old in out_dir.glob("reel.*"):
        old.unlink()

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    print(f"[reel_pipeline] Downloading: {url}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed (exit {result.returncode}):\n{result.stderr[-600:]}"
        )

    files = sorted(out_dir.glob("reel.*"))
    if not files:
        raise RuntimeError(
            f"yt-dlp reported success but no file found in {out_dir}.\n"
            f"stdout: {result.stdout[-300:]}"
        )

    video_path = files[0]
    print(f"[reel_pipeline] Downloaded → {video_path} ({video_path.stat().st_size // 1024} KB)", flush=True)
    return video_path


# ── Part 2 — Extract first frame ─────────────────────────────────────────────

def extract_first_frame(video_path: Path, out_path: Path) -> Path:
    """
    Extract the first frame of a video as a JPEG using ffmpeg.
    Returns the path to the saved frame image.
    Raises RuntimeError on failure.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found. Install with: brew install ffmpeg"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(out_path),
    ]
    print(f"[reel_pipeline] Extracting first frame → {out_path.name}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"ffmpeg frame extraction failed:\n{result.stderr[-600:]}"
        )

    print(f"[reel_pipeline] Frame saved ({out_path.stat().st_size // 1024} KB)", flush=True)
    return out_path


# ── Part 3+4 — Prompt + Nano Banana ──────────────────────────────────────────
# These steps are handled by generate_images.fill_prompt_with_claude()
# and generate_images.generate_base() — called directly from app.py.
# No changes to those functions.


# ── Part 5 — Kling video generation ──────────────────────────────────────────

async def _kling_async(
    generated_image_path: Path,
    original_video_path: Path,
    out_path: Path,
    api_key: str,
    duration: int = 5,
    aspect_ratio: str = "9:16",
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        print("[kling] Uploading AI-generated image…", flush=True)
        image_url = await ws.upload_file(session, generated_image_path, api_key)

        print("[kling] Uploading original reel video…", flush=True)
        video_url = await ws.upload_file(session, original_video_path, api_key)

        payload = {
            "image":        image_url,
            "prompt":       KLING_MOTION_PROMPT,
            "duration":     duration,
            "aspect_ratio": aspect_ratio,
        }
        print(f"[kling] Model: {KLING_MODEL}", flush=True)
        print(f"[kling] Payload: {payload}", flush=True)

        task_id, poll_url = await ws.submit_job(session, KLING_MODEL, payload, api_key)
        output_url = await ws.poll_job(
            session, task_id, poll_url, api_key,
            max_polls=90, interval=10,   # up to 15 min — Kling jobs are slow
        )
        await ws.download_file(session, output_url, out_path)

    print(f"[kling] Video saved → {out_path}", flush=True)
    return out_path


def generate_kling_video(
    generated_image_path: Path,
    original_video_path: Path,
    out_path: Path,
    api_key: str | None = None,
    duration: int = 5,
    aspect_ratio: str = "9:16",
) -> Path:
    """
    Generate a Kling video using the AI image as the subject
    and the original reel for motion/scene reference.
    Returns path to the output video.
    Raises RuntimeError on failure.
    """
    if api_key is None:
        api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        raise ValueError("WAVESPEED_API_KEY not set in .env")

    return asyncio.run(
        _kling_async(
            generated_image_path,
            original_video_path,
            out_path,
            api_key,
            duration=duration,
            aspect_ratio=aspect_ratio,
        )
    )
