"""
editor/cutmap.py — Layer 3 core: the cut map (the contract).

The cut map is a plain-JSON edit decision list of keep/cut segments covering
the whole timeline. Detection only *proposes* cuts; the LLM pass and the human
reviewer are free to flip any `action` or add `protect: true` to shield an
emphasis/comedic pause. Every later step (render, captions) reads this file —
nothing is cut that the map doesn't explicitly mark.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import annotate as _annotate
from . import config


def _emit_gap(atoms: list[dict], g0: float, g1: float) -> None:
    """Append keep/cut atoms for a gap between speech."""
    dur = g1 - g0
    if dur <= 0:
        return
    if dur > config.SILENCE_THRESHOLD:
        cut_start = g0 + config.KEEP_PAD
        cut_end = g1 - config.KEEP_PAD
        if cut_end - cut_start >= config.MIN_CUT:
            atoms.append({"start": g0, "end": cut_start, "action": "keep", "text": ""})
            atoms.append({
                "start": cut_start, "end": cut_end, "action": "cut",
                "type": "silence", "reason": f"dead air {dur:.1f}s",
            })
            atoms.append({"start": cut_end, "end": g1, "action": "keep", "text": ""})
            return
    atoms.append({"start": g0, "end": g1, "action": "keep", "text": ""})


def _coalesce(atoms: list[dict]) -> list[dict]:
    """Merge adjacent atoms with the same action (and matching cut type)."""
    segments: list[dict] = []
    for a in atoms:
        if a["start"] >= a["end"]:
            continue
        prev = segments[-1] if segments else None
        same = (
            prev is not None
            and prev["action"] == a["action"]
            and prev.get("type") == a.get("type")
        )
        if same:
            prev["end"] = a["end"]
            text = (prev.get("text", "") + " " + a.get("text", "")).strip()
            prev["text"] = text
        else:
            segments.append(dict(a))
    return segments


def build_cutmap(annotated: dict) -> dict:
    """Build a proposed cut map from an annotated transcript."""
    duration = annotated["duration"]
    words = annotated["words"]

    atoms: list[dict] = []
    prev_end = 0.0
    for w in words:
        _emit_gap(atoms, prev_end, w["start"])
        if w.get("filler"):
            atoms.append({
                "start": w["start"], "end": w["end"], "action": "cut",
                "type": "filler", "reason": "filler", "text": w["word"],
            })
        else:
            atoms.append({
                "start": w["start"], "end": w["end"], "action": "keep",
                "text": w["word"],
            })
        prev_end = w["end"]
    _emit_gap(atoms, prev_end, duration)

    segments = _coalesce(atoms)
    for i, s in enumerate(segments):
        s["id"] = i
        s["start"] = round(max(0.0, s["start"]), 3)
        s["end"] = round(min(duration, s["end"]), 3)
        if not s.get("text"):
            s.pop("text", None)

    cutmap = {
        "source": annotated["source"],
        "duration": duration,
        "stats": _cut_stats(segments, duration),
        "segments": segments,
    }
    return cutmap


def effective_action(seg: dict) -> str:
    """A protected segment is always kept, whatever its proposed action."""
    return "keep" if seg.get("protect") else seg["action"]


def keep_segments(cutmap: dict) -> list[dict]:
    """Kept segments in timeline order (honors `protect`)."""
    return [s for s in cutmap["segments"] if effective_action(s) == "keep"]


def _cut_stats(segments: list[dict], duration: float) -> dict:
    kept = sum(s["end"] - s["start"] for s in segments if effective_action(s) == "keep")
    fillers = [s for s in segments if effective_action(s) == "cut" and s.get("type") == "filler"]
    silences = [s for s in segments if effective_action(s) == "cut" and s.get("type") == "silence"]
    return {
        "original_duration": round(duration, 2),
        "edited_duration": round(kept, 2),
        "removed_seconds": round(duration - kept, 2),
        "filler_cuts": len(fillers),
        "silence_cuts": len(silences),
    }


def summarize(cutmap: dict) -> str:
    st = _cut_stats(cutmap["segments"], cutmap["duration"])
    pct = (st["removed_seconds"] / st["original_duration"] * 100) if st["original_duration"] else 0
    lines = [
        f"Source:   {cutmap['source']}",
        f"Original: {st['original_duration']:.1f}s   "
        f"Edited: {st['edited_duration']:.1f}s   "
        f"Removed: {st['removed_seconds']:.1f}s ({pct:.0f}%)",
        f"Cuts:     {st['filler_cuts']} filler, {st['silence_cuts']} silence",
        "",
    ]
    for s in cutmap["segments"]:
        act = effective_action(s)
        tag = "KEEP" if act == "keep" else f"CUT/{s.get('type', '?')}"
        flag = " [protected]" if s.get("protect") else ""
        detail = s.get("text") or s.get("reason", "")
        if len(detail) > 64:
            detail = detail[:61] + "…"
        lines.append(f"  [{s['id']:>3}] {s['start']:7.2f}–{s['end']:7.2f}  {tag:<12}{flag} {detail}")
    return "\n".join(lines)


def save_cutmap(cutmap: dict, project_dir: Path) -> Path:
    path = project_dir / "cutmap.json"
    path.write_text(json.dumps(cutmap, indent=2))
    return path


def load_cutmap(project_dir: Path) -> dict:
    path = project_dir / "cutmap.json"
    if not path.exists():
        raise FileNotFoundError(f"No cut map at {path}. Run `plan` first.")
    return json.loads(path.read_text())


def plan(transcript: dict) -> dict:
    """Annotate a transcript and build its cut map."""
    return build_cutmap(_annotate.annotate(transcript))
