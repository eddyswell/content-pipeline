import os
import sys
import shutil
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from moviepy import VideoFileClip, TextClip, CompositeVideoClip
import config

load_dotenv()


def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg is not installed or not in PATH.")
        print("Install via: brew install ffmpeg  (macOS) or apt install ffmpeg (Linux)")
        sys.exit(1)


def transcribe_audio(client: OpenAI, audio_path: Path) -> list[dict]:
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = []
    raw_words = getattr(result, "words", None) or []
    for w in raw_words:
        words.append({"word": str(w.word).strip(), "start": float(w.start), "end": float(w.end)})
    return words


def write_srt(words: list[dict], srt_path: Path):
    with open(srt_path, "w") as f:
        for i, w in enumerate(words, 1):
            start = _fmt_srt(w["start"])
            end = _fmt_srt(w["end"])
            f.write(f"{i}\n{start} --> {end}\n{w['word']}\n\n")


def _fmt_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
CAPTION_COLOR = "#D4A017"   # deep autumn yellow
FONT_SIZE = 42
STROKE_WIDTH = 3
Y_POSITION = 0.78           # % down the screen


def burn_captions(video_path: Path, words: list[dict], out_path: Path):
    video = VideoFileClip(str(video_path))
    _, h = video.size

    clips = [video]

    for word in words:
        txt = (
            TextClip(
                font=FONT,
                text=word["word"].upper(),
                font_size=FONT_SIZE,
                color=CAPTION_COLOR,
                stroke_color="black",
                stroke_width=STROKE_WIDTH,
                method="label",
            )
            .with_start(word["start"])
            .with_end(word["end"])
            .with_position(("center", int(h * Y_POSITION)))
        )
        clips.append(txt)

    final = CompositeVideoClip(clips)
    final.write_videofile(
        str(out_path),
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )
    video.close()
    final.close()


def parse_srt(srt_path: Path) -> list[dict]:
    words = []
    blocks = srt_path.read_text().strip().split("\n\n")
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        times = lines[1].split(" --> ")
        start = _srt_to_sec(times[0].strip())
        end = _srt_to_sec(times[1].strip())
        words.append({"word": lines[2].strip(), "start": start, "end": end})
    return words


def _srt_to_sec(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def run():
    check_ffmpeg()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in .env")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    video_dir = Path(config.OUTPUT_VIDEOS_DIR)
    voice_dir = Path(config.OUTPUT_VOICES_DIR)
    out_dir = Path(config.OUTPUT_FINAL_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    video_files = sorted(video_dir.glob("video_*.mp4"))
    if not video_files:
        print("No video files found in output/videos/. Run animate_batch.py first.")
        sys.exit(1)

    total = len(video_files)
    done = 0

    for video_path in video_files:
        num = video_path.stem.split("_")[-1]
        voice_path = voice_dir / f"voice_{num}.mp3"
        srt_path = voice_dir / f"voice_{num}.srt"
        out_path = out_dir / f"final_{num}.mp4"

        if out_path.exists():
            print(f"Skipping final_{num}.mp4: already exists")
            done += 1
            continue

        if not voice_path.exists():
            print(f"  WARNING: No matching audio for {video_path.name}, skipping.")
            continue

        print(f"Processing {video_path.name}...")

        try:
            if srt_path.exists():
                print(f"  Using cached transcript: {srt_path.name}")
                words = parse_srt(srt_path)
            else:
                print(f"  Transcribing {voice_path.name}...")
                words = transcribe_audio(client, voice_path)
                write_srt(words, srt_path)
                print(f"  SRT saved: {srt_path.name}")

            print(f"  Burning captions into {video_path.name}...")
            burn_captions(video_path, words, out_path)
            print(f"  Saved: {out_path.name}")
            done += 1

        except Exception as e:
            print(f"  ERROR processing {video_path.name}: {e}")

    print(f"\nDone. {done}/{total} final videos produced.")


if __name__ == "__main__":
    run()
