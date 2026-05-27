"""
editor/transcribe.py — Layer 1: turn a video into a word-level transcript.

Extracts the audio track, sends it to OpenAI Whisper for word-timestamped
transcription, and writes a structured transcript.json — the single artifact
every later layer reads from.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from openai import OpenAI

from . import config


def _require(tool: str) -> None:
    if not shutil.which(tool):
        raise RuntimeError(f"{tool} not found on PATH. Install it and retry.")


def probe_duration(media_path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    _require("ffprobe")
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{out.stderr[-400:]}")
    return float(out.stdout.strip())


def extract_audio(video_path: Path, out_path: Path) -> Path:
    """Extract mono 16 kHz mp3 audio — small enough for the Whisper API."""
    _require("ffmpeg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(config.AUDIO_SAMPLE_RATE),
        "-b:a", config.AUDIO_BITRATE, str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Audio extraction failed:\n{result.stderr[-400:]}")
    size_mb = out_path.stat().st_size / 1e6
    if size_mb > 24:
        raise RuntimeError(
            f"Extracted audio is {size_mb:.1f} MB, over Whisper's 25 MB limit. "
            "Chunked transcription is not implemented yet — split the video first."
        )
    return out_path


def transcribe_audio(client: OpenAI, audio_path: Path) -> list[dict]:
    """Return word-level timestamps: [{word, start, end}, ...]."""
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=config.WHISPER_MODEL,
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = []
    for w in getattr(result, "words", None) or []:
        words.append(
            {"word": str(w.word).strip(), "start": float(w.start), "end": float(w.end)}
        )
    return words


def transcribe(video_path: Path, project_dir: Path) -> dict:
    """
    Full Layer 1 for one video. Writes transcript.json into project_dir and
    returns the transcript dict.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in .env")

    project_dir.mkdir(parents=True, exist_ok=True)
    audio_path = project_dir / "audio.mp3"

    print(f"[transcribe] Extracting audio → {audio_path.name}", flush=True)
    extract_audio(video_path, audio_path)

    duration = probe_duration(video_path)

    print("[transcribe] Transcribing via Whisper…", flush=True)
    client = OpenAI(api_key=api_key)
    words = transcribe_audio(client, audio_path)

    transcript = {
        "source": str(video_path.resolve()),
        "duration": duration,
        "text": " ".join(w["word"] for w in words),
        "words": words,
    }
    save_transcript(transcript, project_dir)
    print(f"[transcribe] {len(words)} words over {duration:.1f}s", flush=True)
    return transcript


def save_transcript(transcript: dict, project_dir: Path) -> Path:
    path = project_dir / "transcript.json"
    path.write_text(json.dumps(transcript, indent=2))
    return path


def load_transcript(project_dir: Path) -> dict:
    path = project_dir / "transcript.json"
    if not path.exists():
        raise FileNotFoundError(f"No transcript at {path}. Run `transcribe` first.")
    return json.loads(path.read_text())
