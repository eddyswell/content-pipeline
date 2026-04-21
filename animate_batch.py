import os
import sys
import ssl
import random
import asyncio
import aiohttp
import aiofiles
import certifi
from pathlib import Path
from dotenv import load_dotenv
import config

load_dotenv()

HEYGEN_BASE = "https://api.heygen.com"
HEYGEN_UPLOAD = "https://upload.heygen.com"
POLL_INTERVAL = 15
MAX_POLLS = 20
MAX_CONCURRENT = 3


async def upload_audio(session: aiohttp.ClientSession, file_path: Path, api_key: str) -> str:
    headers = {"X-Api-Key": api_key, "Content-Type": "audio/mpeg"}
    async with aiofiles.open(file_path, "rb") as f:
        data = await f.read()
    async with session.post(f"{HEYGEN_UPLOAD}/v1/asset", data=data, headers=headers) as resp:
        body = await resp.text()
        if not resp.ok:
            raise RuntimeError(f"Audio upload failed {resp.status}: {body}")
        import json
        result = json.loads(body)
        return result["data"]["id"]


async def submit_video_job(
    session: aiohttp.ClientSession,
    talking_photo_id: str,
    audio_id: str,
    api_key: str,
) -> str:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "talking_photo",
                    "talking_photo_id": talking_photo_id,
                    "use_avatar_iv_model": True,
                    "motion_prompt": "woman speaking naturally to camera, relaxed upper body movement, casual hand gestures, confident and expressive",
                },
                "voice": {"type": "audio", "audio_asset_id": audio_id},
            }
        ],
        "dimension": {"width": 720, "height": 1280},
    }
    async with session.post(f"{HEYGEN_BASE}/v2/video/generate", json=payload, headers=headers) as resp:
        body = await resp.text()
        if not resp.ok:
            raise RuntimeError(f"Video submit failed {resp.status}: {body}")
        import json
        result = json.loads(body)
        return result["data"]["video_id"]


async def poll_video(session: aiohttp.ClientSession, video_id: str, api_key: str) -> str | None:
    headers = {"X-Api-Key": api_key}
    for attempt in range(MAX_POLLS):
        await asyncio.sleep(POLL_INTERVAL)
        async with session.get(
            f"{HEYGEN_BASE}/v1/video_status.get",
            params={"video_id": video_id},
            headers=headers,
        ) as resp:
            body = await resp.text()
            if not resp.ok:
                print(f"  Poll {attempt+1}: HTTP {resp.status} — {body[:200]}")
                continue
            import json
            result = json.loads(body)
            data = result.get("data", {})
            status = data.get("status", "unknown")
            print(f"  Poll {attempt+1} [{video_id[:8]}]: status={status}")
            if status == "completed":
                return data.get("video_url")
            elif status == "failed":
                print(f"  Failed detail: {data}")
                return None
    return None


async def download_video(session: aiohttp.ClientSession, url: str, out_path: Path):
    async with session.get(url) as resp:
        resp.raise_for_status()
        async with aiofiles.open(out_path, "wb") as f:
            await f.write(await resp.read())


async def process_video(
    semaphore: asyncio.Semaphore,
    session: aiohttp.ClientSession,
    index: int,
    total: int,
    voice_path: Path,
    out_path: Path,
    talking_photo_id: str,
    api_key: str,
):
    async with semaphore:
        label = f"video {index}/{total}"
        if out_path.exists():
            print(f"Skipping {label}: already exists ({out_path.name})")
            return True

        print(f"Uploading audio for {label} (avatar: {talking_photo_id[:8]}...)...")
        try:
            audio_id = await upload_audio(session, voice_path, api_key)
            video_id = await submit_video_job(session, talking_photo_id, audio_id, api_key)
            print(f"Job submitted for {label}. Polling...")
            video_url = await poll_video(session, video_id, api_key)
            if not video_url:
                print(f"  ERROR: {label} failed or timed out.")
                return False
            print(f"Video {index} complete. Downloading...")
            await download_video(session, video_url, out_path)
            print(f"Video {index} saved.")
            return True
        except Exception as e:
            print(f"  ERROR processing {label}: {e}")
            return False


def run(avatar_ids_override: list[str] | None = None):
    api_key = os.getenv("HEYGEN_API_KEY", "")
    if not api_key:
        print("ERROR: HEYGEN_API_KEY not set in .env")
        sys.exit(1)

    # Use override IDs (from uploaded images) or fall back to config
    avatar_ids = avatar_ids_override if avatar_ids_override else config.HEYGEN_AVATAR_IDS
    if not avatar_ids:
        print("ERROR: No avatar IDs available. Upload images or set HEYGEN_AVATAR_IDS in config.py")
        sys.exit(1)

    voice_dir = Path(config.OUTPUT_VOICES_DIR)
    out_dir = Path(config.OUTPUT_VIDEOS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    voice_files = sorted(voice_dir.glob("voice_*.mp3"))
    if not voice_files:
        print("No voice files found in output/voices/. Run generate_voices.py first.")
        sys.exit(1)

    total = len(voice_files)
    jobs = []
    for i, voice_path in enumerate(voice_files, 1):
        num = voice_path.stem.split("_")[-1]
        out_path = out_dir / f"video_{num}.mp4"
        # When IDs match videos 1:1 use them directly; otherwise cycle
        avatar_id = avatar_ids[(i - 1) % len(avatar_ids)]
        jobs.append((i, voice_path, out_path, avatar_id))

    async def main():
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                process_video(semaphore, session, i, total, vp, op, av_id, api_key)
                for i, vp, op, av_id in jobs
            ]
            results = await asyncio.gather(*tasks)
        done = sum(1 for r in results if r)
        print(f"\nDone. {done}/{total} videos generated.")

    asyncio.run(main())


if __name__ == "__main__":
    run()
