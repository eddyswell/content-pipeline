"""
generate_images.py — Wavespeed Nano Banana 2 base-image generator.

Flow for each reference image:
  1. Claude (vision) analyzes the reference + fills the master prompt template
  2. Upload model collage + reference image to Wavespeed
  3. Submit job to Nano Banana 2 Edit
  4. Poll until complete → download result

Entry point: run(reference_paths, creator_name, progress_cb) → list[Path]
"""

from __future__ import annotations

import asyncio
import base64
import os
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import anthropic
import certifi
from dotenv import load_dotenv

import wavespeed_utils as ws

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL           = "google/nano-banana-2/edit"
MASTER_PROMPT   = Path(__file__).parent / "assets" / "master_prompt.txt"
COLLAGES_DIR    = Path(__file__).parent / "assets" / "model_collages"
OUTPUT_DIR      = Path(__file__).parent / "output" / "generated_images"

# Hard-coded face orientation constraint — appended to every Nano Banana prompt.
# Ensures consistent front-facing frames for lip-sync quality.
_FACE_LOCK = " Face camera directly. Front-facing portrait only. No side angles or head tilt."

# Hard-coded identity enforcement — appended after _FACE_LOCK on every Nano Banana prompt.
# Explicitly strips identity from the reference image and locks it to the collage.
_IDENTITY_LOCK = (
    " IDENTITY ENFORCEMENT: The first image (collage) defines the subject's face and identity."
    " The second image (reference) defines the scene ONLY — completely ignore the face and"
    " identity of the person in the reference image."
    " The subject's face, skin tone, and features must EXACTLY match the collage identity."
    " Do NOT blend identities. Do NOT mix with the reference image person."
    " Output subject must be unmistakably the collage person."
)

MODEL_COLLAGES: dict[str, Path] = {
    "Fiona (US)": COLLAGES_DIR / "Fiona.png",
    "Lisa (AU)":  COLLAGES_DIR / "Lisa.png",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_collage(creator_name: str) -> Path:
    base = MODEL_COLLAGES.get(creator_name)
    if base is None:
        raise FileNotFoundError(f"No collage configured for: {creator_name}")
    for p in [base, base.with_suffix(".jpg"), base.with_suffix(".jpeg")]:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Model collage for {creator_name} not found. "
        f"Expected: {base}  — add image to assets/model_collages/"
    )


def _media_type(path: Path) -> str:
    with open(path, "rb") as f:
        h = f.read(12)
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def _load_master_prompt() -> str:
    if not MASTER_PROMPT.exists():
        raise FileNotFoundError(f"Master prompt not found: {MASTER_PROMPT}")
    return MASTER_PROMPT.read_text(encoding="utf-8").strip()


# ── Step 1: Claude fills the prompt ──────────────────────────────────────────

def _encode_for_api(path: Path, max_long_side: int = 1568) -> tuple[str, str]:
    """Resize image to ≤max_long_side px, encode as JPEG base64.
    Keeps payloads well under the Anthropic 413 request-size limit."""
    try:
        from PIL import Image
        import io as _io
        with Image.open(path) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_long_side:
                scale = max_long_side / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"
    except ImportError:
        # Pillow not installed — fall back to raw file (may hit size limit on large images)
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode(), _media_type(path)


def fill_prompt_with_claude(collage_path: Path, reference_path: Path, master: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    col_b64, col_mt = _encode_for_api(collage_path)
    ref_b64, ref_mt = _encode_for_api(reference_path)

    client   = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2048,
        system=master,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": col_mt, "data": col_b64}},
                {"type": "image", "source": {"type": "base64", "media_type": ref_mt, "data": ref_b64}},
                {"type": "text",  "text": (
                    "IMAGE 1 is the identity collage — it defines the subject's face, skin tone, "
                    "bone structure, and body proportions EXCLUSIVELY. "
                    "IMAGE 2 is the scene reference — extract ONLY: pose, body positioning, "
                    "camera angle, environment, lighting, composition, and wardrobe structure. "
                    "CRITICAL: completely ignore the face and identity of the person in IMAGE 2. "
                    "Do NOT replicate their facial features, skin tone, or any identity marker. "
                    "The output subject must match IMAGE 1's person exactly, placed into IMAGE 2's scene. "
                    "Fill the MASTER PROMPT TEMPLATE completely. Output ONLY the filled template."
                )},
            ],
        }],
    )
    return response.content[0].text.strip()


# ── Async pipeline ────────────────────────────────────────────────────────────

async def _generate_all(
    reference_paths: list[Path],
    collage_path:    Path,
    master_prompt:   str,
    api_key:         str,
    progress_cb,
) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    generated: list[Path] = []

    async with aiohttp.ClientSession(connector=connector) as session:
        # Upload collage once
        progress_cb(0, len(reference_paths), "Uploading model collage to Wavespeed…")
        collage_url = await ws.upload_file(session, collage_path, api_key)

        for i, ref_path in enumerate(reference_paths, 1):
            total = len(reference_paths)
            label = f"{i}/{total}"

            progress_cb(i, total, f"[{label}] Analysing reference with Claude…")
            filled = fill_prompt_with_claude(collage_path, ref_path, master_prompt)

            progress_cb(i, total, f"[{label}] Uploading reference image…")
            ref_url = await ws.upload_file(session, ref_path, api_key)

            progress_cb(i, total, f"[{label}] Submitting to Nano Banana…")
            payload = {
                "images":        [collage_url, ref_url],
                "prompt":        filled,
                "resolution":    "1k",
                "output_format": "jpeg",
                "aspect_ratio":  "9:16",
            }
            task_id, poll_url = await ws.submit_job(session, MODEL, payload, api_key)

            progress_cb(i, total, f"[{label}] Waiting for generation…")
            output_url = await ws.poll_job(session, task_id, poll_url, api_key)

            out_path = OUTPUT_DIR / f"generated_{i:02d}.jpg"
            await ws.download_file(session, output_url, out_path)
            generated.append(out_path)
            progress_cb(i, total, f"[{label}] Done ✓  →  {out_path.name}")

    return generated


# ── Batch parallel generation ─────────────────────────────────────────────────

@dataclass
class TaskResult:
    idx:      int
    ref_path: Path
    out_path: Path | None
    status:   str        # "success" | "failed"
    error:    str = field(default="")


async def _run_one_with_retry(
    session:     aiohttp.ClientSession,
    semaphore:   asyncio.Semaphore,
    idx:         int,
    ref_path:    Path,
    collage_url: str,
    prompt:      str,
    api_key:     str,
    max_retries: int = 2,
    out_path:    Path | None = None,   # explicit output path; defaults to OUTPUT_DIR/base_{idx}.jpg
) -> TaskResult:
    """Submit + poll one image, with retries. Always returns a TaskResult."""
    if out_path is None:
        out_path = OUTPUT_DIR / f"base_{idx:02d}.jpg"

    for attempt in range(max_retries + 1):
        try:
            async with semaphore:
                ref_url    = await ws.upload_file(session, ref_path, api_key)
                final_prompt = prompt + _FACE_LOCK + _IDENTITY_LOCK

                # ── DEBUG: log identity reference and final prompt ─────────────
                print(f"\n[generate_images] idx={idx} attempt={attempt}", flush=True)
                print(f"[generate_images] IDENTITY REF (collage): used as IMAGE 1 in payload", flush=True)
                print(f"[generate_images] SCENE REF   (reference): {ref_path.name}", flush=True)
                print(f"[generate_images] FINAL PROMPT ({len(final_prompt)} chars):\n{final_prompt}\n", flush=True)
                # ─────────────────────────────────────────────────────────────

                payload = {
                    "images":        [collage_url, ref_url],
                    "prompt":        final_prompt,
                    "resolution":    "1k",
                    "output_format": "jpeg",
                    "aspect_ratio":  "9:16",
                }
                task_id, poll_url = await ws.submit_job(session, MODEL, payload, api_key)
                output_url        = await ws.poll_job(session, task_id, poll_url, api_key)
                await ws.download_file(session, output_url, out_path)
            return TaskResult(idx=idx, ref_path=ref_path, out_path=out_path, status="success")
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                await asyncio.sleep(5 * (attempt + 1))   # brief back-off before retry

    return TaskResult(idx=idx, ref_path=ref_path, out_path=None,
                      status="failed", error=last_error)


async def _batch_async(
    ref_paths:      list[Path],
    collage_path:   Path,
    prompts:        list[str],          # one prompt per ref_path — strict 1:1
    api_key:        str,
    max_concurrent: int = 4,
    max_retries:    int = 2,
    status_cb=None,
    indices:        list[int] | None = None,
    out_paths:      list[Path] | None = None,  # explicit output paths per task
) -> list[TaskResult]:
    assert len(prompts) == len(ref_paths), "prompts length must match ref_paths"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=max_concurrent + 2)
    semaphore = asyncio.Semaphore(max_concurrent)

    orig_indices      = indices   if indices   is not None else list(range(len(ref_paths)))
    resolved_out_paths = out_paths if out_paths is not None else [None] * len(ref_paths)
    assert len(orig_indices)       == len(ref_paths), "indices length must match ref_paths"
    assert len(resolved_out_paths) == len(ref_paths), "out_paths length must match ref_paths"

    async with aiohttp.ClientSession(connector=connector) as session:
        if status_cb:
            status_cb(-1, "Uploading model collage…")
        collage_url = await ws.upload_file(session, collage_path, api_key)

        if status_cb:
            for orig_idx in orig_indices:
                status_cb(orig_idx, "queued")

        async def _wrap(orig_idx, ref_path, prompt, out_path):
            if status_cb:
                status_cb(orig_idx, "uploading")
            result = await _run_one_with_retry(
                session, semaphore, orig_idx, ref_path, collage_url,
                prompt, api_key, max_retries, out_path=out_path,
            )
            if status_cb:
                status_cb(orig_idx, "done" if result.status == "success"
                          else f"failed: {result.error[:60]}")
            return result

        tasks = [
            _wrap(orig_idx, p, prompt, op)
            for orig_idx, p, prompt, op
            in zip(orig_indices, ref_paths, prompts, resolved_out_paths)
        ]
        results = await asyncio.gather(*tasks)

    return list(results)


def generate_batch_parallel(
    ref_paths:      list[Path],
    collage_path:   Path,
    prompts:        list[str],
    api_key:        str | None = None,
    max_concurrent: int = 4,
    max_retries:    int = 2,
    status_cb=None,
    indices:        list[int] | None = None,
    out_paths:      list[Path] | None = None,  # custom output paths (one per ref); None = default
) -> list[TaskResult]:
    """
    Generate base images for all ref_paths IN PARALLEL, each using its own prompt.
    Returns a TaskResult for every input (successes and failures).

    Args:
        ref_paths:       List of reference image paths.
        collage_path:    Model identity collage (uploaded once, reused).
        prompts:         Per-image prompts — prompts[i] is used exclusively for
                         ref_paths[i].  Must be the same length as ref_paths.
        api_key:         Wavespeed API key (reads from env if None).
        max_concurrent:  Max simultaneous Nano Banana jobs (default 4).
        max_retries:     Per-task retry count on failure (default 2).
        status_cb:       Optional callable(idx, status_str) for live feedback.
        indices:         Original indices for each ref_path (controls output filename
                         base_{idx:02d}.jpg). Defaults to 0, 1, 2, … when None.
                         Must be provided when retrying a subset of images so that
                         original reference[i] → base_{i:02d}.jpg is always preserved.
    """
    if api_key is None:
        api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        raise ValueError("WAVESPEED_API_KEY not set in .env")
    return asyncio.run(
        _batch_async(ref_paths, collage_path, prompts, api_key,
                     max_concurrent, max_retries, status_cb, indices, out_paths)
    )


# ── Single-image helpers (used by debug UI) ───────────────────────────────────

def get_collage_path(creator_name: str) -> Path:
    """Return the resolved collage path for a creator (raises if missing)."""
    return _resolve_collage(creator_name)


async def _generate_base_async(
    collage_path: Path,
    ref_path: Path,
    filled_prompt: str,
    out_path: Path,
    api_key: str,
) -> Path:
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        collage_url = await ws.upload_file(session, collage_path, api_key)
        ref_url     = await ws.upload_file(session, ref_path, api_key)
        payload = {
            "images":        [collage_url, ref_url],
            "prompt":        filled_prompt,
            "resolution":    "1k",
            "output_format": "jpeg",
            "aspect_ratio":  "9:16",
        }
        task_id, poll_url = await ws.submit_job(session, MODEL, payload, api_key)
        output_url        = await ws.poll_job(session, task_id, poll_url, api_key)
        await ws.download_file(session, output_url, out_path)
    return out_path


def generate_base(
    collage_path: Path,
    ref_path: Path,
    filled_prompt: str,
    out_path: Path,
    api_key: str | None = None,
) -> Path:
    """
    Generate ONE base image using a pre-filled prompt (Claude step already done).
    Called by the debug UI after the user has reviewed / edited the prompt.
    """
    if api_key is None:
        api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        raise ValueError("WAVESPEED_API_KEY not set in .env")
    return asyncio.run(_generate_base_async(collage_path, ref_path, filled_prompt, out_path, api_key))


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    reference_paths: list[Path],
    creator_name:    str,
    progress_cb=None,
) -> list[Path]:
    api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        raise ValueError("WAVESPEED_API_KEY not set in .env")

    collage_path  = _resolve_collage(creator_name)
    master_prompt = _load_master_prompt()
    if progress_cb is None:
        progress_cb = lambda i, t, m: print(f"  [{i}/{t}] {m}", flush=True)

    return asyncio.run(
        _generate_all(reference_paths, collage_path, master_prompt, api_key, progress_cb)
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 generate_images.py <creator_name> <img1> [img2 ...]")
        sys.exit(1)
    results = run([Path(p) for p in sys.argv[2:]], sys.argv[1])
    for p in results:
        print(p)
