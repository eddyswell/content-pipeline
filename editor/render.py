"""
editor/render.py — Layer 3: execute the cut map with ffmpeg.

Concatenates the kept segments in a single re-encode pass (re-encoding is
required for frame-accurate cuts) and optionally applies EBU R128 loudness
normalization. The filtergraph is written to a script file so the command
never hits shell argument-length limits, however many cuts there are.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config
from . import cutmap as _cutmap


def _build_filtergraph(segments: list[dict], normalize: bool) -> str:
    parts = []
    for i, s in enumerate(segments):
        parts.append(
            f"[0:v]trim=start={s['start']}:end={s['end']},"
            f"setpts=PTS-STARTPTS[v{i}];"
        )
        parts.append(
            f"[0:a]atrim=start={s['start']}:end={s['end']},"
            f"asetpts=PTS-STARTPTS[a{i}];"
        )
    streams = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    parts.append(f"{streams}concat=n={len(segments)}:v=1:a=1[vout][araw];")
    if normalize:
        parts.append(
            f"[araw]loudnorm=I={config.LOUDNORM_I}:TP={config.LOUDNORM_TP}:"
            f"LRA={config.LOUDNORM_LRA}[aout]"
        )
    else:
        parts.append("[araw]anull[aout]")
    return "\n".join(parts)


def render(cutmap: dict, out_path: Path, normalize: bool | None = None) -> Path:
    """Render the kept segments of `cutmap` to out_path."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH.")

    if normalize is None:
        normalize = config.NORMALIZE_AUDIO

    source = Path(cutmap["source"])
    if not source.exists():
        raise FileNotFoundError(f"Source video not found: {source}")

    segments = _cutmap.keep_segments(cutmap)
    if not segments:
        raise RuntimeError("Cut map keeps nothing — every segment is cut.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph = _build_filtergraph(segments, normalize)
    graph_path = out_path.parent / "filtergraph.txt"
    graph_path.write_text(graph)

    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-filter_complex_script", str(graph_path),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", config.VIDEO_CODEC, "-preset", config.VIDEO_PRESET,
        "-crf", str(config.VIDEO_CRF),
        "-c:a", config.AUDIO_CODEC, "-b:a", config.AUDIO_BITRATE_OUT,
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"[render] {len(segments)} segments → {out_path.name}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg render failed:\n{result.stderr[-800:]}")

    graph_path.unlink(missing_ok=True)
    print(f"[render] Done → {out_path}", flush=True)
    return out_path
