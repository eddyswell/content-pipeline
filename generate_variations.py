"""
generate_variations.py — Seedream V4.5 image variation generator.

Takes a base image (from Nano Banana) and produces N slight variations:
  - same identity, outfit, environment
  - varied angle / framing / expression
  - improved skin-texture realism

Entry points:
  run_all(base_path, base_idx, creator_name, n=5, progress_cb=None) → list[Path]
  run_single(base_path, base_idx, var_idx, creator_name)             → Path
"""

from __future__ import annotations

import asyncio
import os
import ssl
from pathlib import Path

import aiohttp
import certifi
from dotenv import load_dotenv

import wavespeed_utils as ws

load_dotenv()

# ── Model ─────────────────────────────────────────────────────────────────────

MODEL = "bytedance/seedream-v4.5/edit"

# ── Variation prompts ─────────────────────────────────────────────────────────
# Each prompt describes only WHAT to vary; identity/outfit/environment stay locked.

VARIATION_PROMPTS: list[str] = [
    (
        "Maintain the subject's exact facial identity, hair, outfit, and background. "
        "Shift the head angle very slightly to the left. Candid, natural expression. "
        "Enhance skin texture realism — subtle natural imperfections, no beauty filter."
    ),
    (
        "Maintain the subject's exact facial identity, hair, outfit, and background. "
        "Shift the head angle very slightly to the right. Relaxed, natural expression. "
        "Enhance skin texture realism — subtle natural imperfections, no beauty filter."
    ),
    (
        "Maintain the subject's exact facial identity, hair, outfit, and background. "
        "Widen the framing very slightly to show a touch more of the environment. "
        "Natural candid moment. Enhance skin texture realism."
    ),
    (
        "Maintain the subject's exact facial identity, hair, outfit, and background. "
        "Tighten the framing slightly — more intimate portrait crop. "
        "Direct, confident gaze into camera. Enhance skin texture realism."
    ),
    (
        "Maintain the subject's exact facial identity, hair, outfit, and background. "
        "Subtle upward chin tilt, poised and confident expression. "
        "Enhance skin texture realism — subtle natural imperfections, no beauty filter."
    ),
]

OUTPUT_DIR = Path("output/variations")

# Hard-coded face orientation constraint — appended to every Seedream prompt.
# Keeps the face front-facing across all variations for consistent lip-sync.
_FACE_LOCK = (
    " CRITICAL REQUIREMENT: The subject's face must remain perfectly front-facing "
    "and symmetrical, eyes looking directly into the camera lens. "
    "No profile view, no side angle, no head tilt, no looking away."
)


# ── Core async helpers ────────────────────────────────────────────────────────

async def _generate_one(
    session: aiohttp.ClientSession,
    base_url: str,
    var_idx: int,
    out_path: Path,
    api_key: str,
    prompt: str | None = None,          # None → use default for var_idx
) -> Path:
    if prompt is None:
        prompt = VARIATION_PROMPTS[var_idx % len(VARIATION_PROMPTS)]
    payload = {
        "images": [base_url],
        "prompt": prompt + _FACE_LOCK,
        "width":  720,
        "height": 1280,
    }
    task_id, poll_url = await ws.submit_job(session, MODEL, payload, api_key)
    output_url        = await ws.poll_job(session, task_id, poll_url, api_key)
    await ws.download_file(session, output_url, out_path)
    return out_path


async def _run_all_async(
    base_path: Path,
    base_idx: int,
    api_key: str,
    n: int,
    progress_cb,
    out_dir: Path | None = None,   # save to this dir instead of OUTPUT_DIR
) -> list[Path]:
    actual_dir = out_dir or OUTPUT_DIR
    actual_dir.mkdir(parents=True, exist_ok=True)
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        progress_cb(0, n, "Uploading base image to Wavespeed…")
        base_url = await ws.upload_file(session, base_path, api_key)

        paths: list[Path] = []
        for var_i in range(n):
            out_path = actual_dir / f"v_{var_i:02d}.jpg"
            progress_cb(var_i, n, f"Generating variation {var_i + 1}/{n}…")
            path = await _generate_one(session, base_url, var_i, out_path, api_key)
            paths.append(path)
            progress_cb(var_i + 1, n, f"Variation {var_i + 1}/{n} done ✓")

    return paths


async def _run_single_async(
    base_path: Path,
    base_idx: int,
    var_idx: int,
    api_key: str,
    prompt: str | None = None,
    out_path: Path | None = None,   # explicit output path; defaults to OUTPUT_DIR/var_*.jpg
) -> Path:
    actual_out = out_path or (OUTPUT_DIR / f"var_{base_idx:02d}_{var_idx:02d}.jpg")
    actual_out.parent.mkdir(parents=True, exist_ok=True)
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        base_url = await ws.upload_file(session, base_path, api_key)
        return await _generate_one(session, base_url, var_idx, actual_out, api_key, prompt)


# ── Public entry points ───────────────────────────────────────────────────────

def run_all(
    base_path: Path,
    base_idx: int,
    creator_name: str = "",
    n: int = 5,
    progress_cb=None,
    out_dir: Path | None = None,   # save variations here; None = legacy OUTPUT_DIR
) -> list[Path]:
    """Generate `n` variations of `base_path`. Returns list of generated Paths."""
    api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        raise ValueError("WAVESPEED_API_KEY not set in .env")
    if progress_cb is None:
        progress_cb = lambda i, total, msg: print(f"  [{i}/{total}] {msg}", flush=True)
    return asyncio.run(_run_all_async(base_path, base_idx, api_key, n, progress_cb, out_dir))


def run_single(
    base_path: Path,
    base_idx: int,
    var_idx: int,
    creator_name: str = "",
    custom_prompt: str | None = None,
    out_path: Path | None = None,   # explicit output path; None = legacy OUTPUT_DIR/var_*.jpg
) -> Path:
    """Regenerate a single variation slot (optionally with a custom prompt)."""
    api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        raise ValueError("WAVESPEED_API_KEY not set in .env")
    return asyncio.run(_run_single_async(base_path, base_idx, var_idx, api_key,
                                         custom_prompt, out_path))
