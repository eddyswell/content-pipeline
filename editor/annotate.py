"""
editor/annotate.py — Layer 1 analysis: enrich a transcript with the signals
the cut map is built from.

Deterministic only. Marks fillers, finds over-long pauses, and reports
speaking rate. It never decides taste questions (is this pause dramatic?) —
that is left to the Layer 2 LLM pass and the human reviewer.
"""

from __future__ import annotations

import re

from . import config

_WORD_RE = re.compile(r"[a-z']+")


def normalize(word: str) -> str:
    """Lowercase and strip surrounding punctuation for matching."""
    m = _WORD_RE.search(word.lower())
    return m.group(0) if m else ""


def filler_flags(words: list[dict]) -> list[bool]:
    """
    Return a parallel list: True where a word is a filler to remove.

    Single non-lexical fillers (um/uh/...) are always flagged. Discourse
    markers and multi-word phrases are flagged only when enabled in config.
    """
    norms = [normalize(w["word"]) for w in words]
    flags = [False] * len(words)

    active_singles = set(config.FILLER_WORDS)
    if config.REMOVE_DISCOURSE_FILLERS:
        active_singles |= set(config.DISCOURSE_FILLERS)

    for i, n in enumerate(norms):
        if n and n in active_singles:
            flags[i] = True

    if config.REMOVE_FILLER_PHRASES:
        for i in range(len(norms) - 1):
            if (norms[i], norms[i + 1]) in config.FILLER_PHRASES:
                flags[i] = flags[i + 1] = True

    return flags


def detect_silences(words: list[dict], duration: float) -> list[dict]:
    """
    Find gaps longer than the threshold: leading silence, inter-word gaps, and
    trailing silence. Returns [{start, end, duration}, ...].
    """
    th = config.SILENCE_THRESHOLD
    silences = []

    if words:
        if words[0]["start"] > th:
            silences.append({"start": 0.0, "end": words[0]["start"]})
        for a, b in zip(words, words[1:]):
            gap = b["start"] - a["end"]
            if gap > th:
                silences.append({"start": a["end"], "end": b["start"]})
        tail = duration - words[-1]["end"]
        if tail > th:
            silences.append({"start": words[-1]["end"], "end": duration})

    for s in silences:
        s["duration"] = round(s["end"] - s["start"], 3)
    return silences


def speaking_rate(words: list[dict]) -> float:
    """Words per minute over the spoken span (ignores leading/trailing silence)."""
    if len(words) < 2:
        return 0.0
    span = words[-1]["end"] - words[0]["start"]
    return round(len(words) / span * 60, 1) if span > 0 else 0.0


def annotate(transcript: dict) -> dict:
    """
    Return an enriched copy: each word gains a `filler` flag, plus top-level
    `silences` and `stats`.
    """
    words = [dict(w) for w in transcript["words"]]
    flags = filler_flags(words)
    for w, f in zip(words, flags):
        w["filler"] = f

    silences = detect_silences(words, transcript["duration"])

    stats = {
        "wpm": speaking_rate(words),
        "filler_count": sum(flags),
        "silence_count": len(silences),
        "silence_seconds": round(sum(s["duration"] for s in silences), 2),
    }

    return {**transcript, "words": words, "silences": silences, "stats": stats}
