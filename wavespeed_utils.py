"""
wavespeed_utils.py — shared Wavespeed API primitives.
Used by generate_images.py and generate_variations.py.
"""

import json
from pathlib import Path

import aiofiles
import aiohttp


WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"


def _media_type_from_bytes(path: Path) -> str:
    with open(path, "rb") as f:
        h = f.read(12)
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp"
    # MP4 / MOV — ftyp box starts at byte 4
    if h[4:8] in (b"ftyp", b"moov", b"mdat"):
        return "video/mp4"
    ext = path.suffix.lower()
    if ext in (".mp4", ".mov", ".m4v", ".webm"):
        return "video/mp4"
    return "image/png" if ext == ".png" else "image/jpeg"


async def upload_file(session: aiohttp.ClientSession, path: Path, api_key: str) -> str:
    """Upload a local file to Wavespeed. Returns the CDN download URL."""
    headers = {"Authorization": f"Bearer {api_key}"}
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    form = aiohttp.FormData()
    form.add_field("file", data, filename=path.name,
                   content_type=_media_type_from_bytes(path))
    async with session.post(
        f"{WAVESPEED_BASE}/media/upload/binary", data=form, headers=headers
    ) as resp:
        body = await resp.text()
        if not resp.ok:
            raise RuntimeError(f"Wavespeed upload failed {resp.status}: {body[:400]}")
        return json.loads(body)["data"]["download_url"]


async def submit_job(
    session: aiohttp.ClientSession,
    model_path: str,
    payload: dict,
    api_key: str,
) -> tuple[str, str]:
    """
    POST a generation job to `model_path` (e.g. 'google/nano-banana-2/edit').
    Returns (task_id, poll_url).
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with session.post(
        f"{WAVESPEED_BASE}/{model_path}", json=payload, headers=headers
    ) as resp:
        body = await resp.text()
        if not resp.ok:
            raise RuntimeError(
                f"Wavespeed submit failed [{model_path}] {resp.status}: {body[:400]}"
            )
        data     = json.loads(body)["data"]
        task_id  = data["id"]
        poll_url = data.get("urls", {}).get("get") or \
                   f"{WAVESPEED_BASE}/predictions/{task_id}/result"
        return task_id, poll_url


async def poll_job(
    session: aiohttp.ClientSession,
    task_id: str,
    poll_url: str,
    api_key: str,
    max_polls: int = 60,   # 60 × 10 s = 600 s (10 min) — enough for any queue
    interval:  int = 10,
) -> str:
    """Poll until the task is complete. Returns the first output URL."""
    import asyncio
    headers = {"Authorization": f"Bearer {api_key}"}
    for attempt in range(max_polls):
        await asyncio.sleep(interval)
        async with session.get(poll_url, headers=headers) as resp:
            body = await resp.text()
            if not resp.ok:
                print(f"  Poll {attempt+1}: HTTP {resp.status}", flush=True)
                continue
            data   = json.loads(body).get("data", {})
            status = data.get("status", "unknown")
            print(f"  Poll {attempt+1} [{task_id[:8]}]: {status}", flush=True)
            if status == "completed":
                outputs = data.get("outputs", [])
                if outputs:
                    return outputs[0]
                raise RuntimeError("Task completed but no output URL returned")
            if status == "failed":
                raise RuntimeError(f"Wavespeed task failed: {data.get('error', data)}")
    raise RuntimeError(f"Wavespeed task timed out after {max_polls * interval}s")


async def download_file(session: aiohttp.ClientSession, url: str, out_path: Path):
    """Download a URL to a local file."""
    async with session.get(url) as resp:
        resp.raise_for_status()
        async with aiofiles.open(out_path, "wb") as f:
            await f.write(await resp.read())
