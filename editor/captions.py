"""
editor/captions.py — Layer 3: word-level captions on the *edited* timeline.

After cutting, original timestamps no longer match the video. This remaps each
surviving word onto the edited timeline (filler/silence words drop out because
they fall inside cut segments), writes a styled ASS subtitle file, and burns it
in with ffmpeg's libass-backed `subtitles` filter.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config
from . import cutmap as _cutmap


def probe_resolution(video_path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", str(video_path),
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{out.stderr[-400:]}")
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


def remap_words(transcript_words: list[dict], cutmap: dict) -> list[dict]:
    """Map surviving words onto the edited timeline. Returns [{start,end,text}]."""
    intervals = _cutmap.keep_segments(cutmap)
    offset = 0.0
    spans = []  # (orig_start, orig_end, new_start)
    for seg in intervals:
        spans.append((seg["start"], seg["end"], offset))
        offset += seg["end"] - seg["start"]

    events = []
    for w in transcript_words:
        mid = (w["start"] + w["end"]) / 2
        for s, e, new_start in spans:
            if s <= mid <= e:
                seg_new_end = new_start + (e - s)
                ns = min(max(new_start + (w["start"] - s), new_start), seg_new_end)
                ne = min(max(new_start + (w["end"] - s), ns + 0.05), seg_new_end)
                events.append({"start": ns, "end": ne, "text": w["word"]})
                break
    return events


def _hex_to_ass(hex_color: str) -> str:
    """#RRGGBB → ASS &HAABBGGRR (opaque)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(events: list[dict], width: int, height: int) -> str:
    font_size = max(20, int(height * config.CAPTION_FONTSIZE_RATIO))
    margin_v = int(height * config.CAPTION_BOTTOM_MARGIN_RATIO)
    primary = _hex_to_ass(config.CAPTION_COLOR)
    outline = _hex_to_ass(config.CAPTION_OUTLINE)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV\n"
        f"Style: Default,{config.CAPTION_FONT},{font_size},{primary},{outline},"
        f"&H00000000,-1,0,1,{config.CAPTION_OUTLINE_WIDTH},0,2,40,40,{margin_v}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text\n"
    )
    lines = [header]
    for ev in events:
        text = ev["text"].strip().replace("\n", " ")
        if config.CAPTION_UPPERCASE:
            text = text.upper()
        lines.append(
            f"Dialogue: 0,{_ass_time(ev['start'])},{_ass_time(ev['end'])},"
            f"Default,,0,0,0,{text}"
        )
    return "\n".join(lines) + "\n"


def burn(video_path: Path, ass_path: Path, out_path: Path) -> Path:
    """Burn an ASS file into a video. Runs ffmpeg from the ASS dir to dodge
    the subtitles-filter path-escaping quirks."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path.resolve()),
        "-vf", f"subtitles={ass_path.name}",
        "-c:v", config.VIDEO_CODEC, "-preset", config.VIDEO_PRESET,
        "-crf", str(config.VIDEO_CRF),
        "-c:a", "copy",
        str(out_path.resolve()),
    ]
    print(f"[captions] Burning {len(ass_path.read_text().splitlines())} lines → {out_path.name}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ass_path.parent))
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Caption burn failed:\n{result.stderr[-800:]}")
    print(f"[captions] Done → {out_path}", flush=True)
    return out_path


def add_captions(transcript: dict, cutmap: dict, edited_video: Path, out_path: Path) -> Path:
    """Full caption step: remap → ASS → burn."""
    events = remap_words(transcript["words"], cutmap)
    width, height = probe_resolution(edited_video)
    ass_path = out_path.parent / "captions.ass"
    ass_path.write_text(build_ass(events, width, height))
    return burn(edited_video, ass_path, out_path)
