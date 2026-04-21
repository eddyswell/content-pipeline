import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv
import config

load_dotenv()


def load_hooks(path: str) -> list[str]:
    hooks = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                hooks.append(line)
    return hooks


def generate_voice(text: str, out_path: Path, api_key: str, voice_id: str) -> bool:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": config.VOICE_SETTINGS,
        "output_format": "mp3_44100_128",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def run(test_mode: bool = False):
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    voice_id = config.ELEVENLABS_VOICE_ID

    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set in .env")
        sys.exit(1)
    if not voice_id:
        print("ERROR: ELEVENLABS_VOICE_ID not set in config.py")
        sys.exit(1)

    out_dir = Path(config.OUTPUT_VOICES_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if test_mode:
        hooks = ["This is a pipeline test"]
        out_files = [out_dir / "test.mp3"]
    else:
        hooks = load_hooks(config.INPUT_HOOKS_FILE)
        out_files = [out_dir / f"voice_{i+1:02d}.mp3" for i in range(len(hooks))]

    total = len(hooks)
    generated = 0

    for i, (hook, out_path) in enumerate(zip(hooks, out_files), 1):
        if out_path.exists():
            print(f"Skipping voice {i}/{total}: already exists ({out_path.name})")
            generated += 1
            continue

        preview = hook[:40] + ("..." if len(hook) > 40 else "")
        print(f"Generating voice {i}/{total}: {preview}")

        success = generate_voice(hook, out_path, api_key, voice_id)
        if success:
            print(f"  Saved: {out_path.name}")
            generated += 1

    print(f"\nDone. {generated}/{total} voices generated.")


if __name__ == "__main__":
    test_mode = "--test" in sys.argv
    run(test_mode=test_mode)
