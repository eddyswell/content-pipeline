"""
editor/config.py — configuration for the editing assistant.

Central place for paths, thresholds, the filler vocabulary, and caption
styling. Mirrors the role of the repo's root config.py but scoped to the
editing pipeline so the two pipelines stay independent.
"""

from __future__ import annotations

# ── Workspace ────────────────────────────────────────────────────────────────
# Each edited video gets its own folder under projects/<name>/ holding the
# transcript, the cut map, and rendered output. Source video is referenced by
# absolute path (not copied) to avoid duplicating large files.
PROJECTS_DIR = "projects"

# ── Transcription ─────────────────────────────────────────────────────────────
WHISPER_MODEL = "whisper-1"          # OpenAI hosted Whisper
# Audio is downmixed to mono 16 kHz before upload — Whisper resamples to 16 kHz
# anyway, and this keeps files well under the 25 MB API limit.
AUDIO_SAMPLE_RATE = 16000
AUDIO_BITRATE = "64k"

# ── Pause / silence detection ─────────────────────────────────────────────────
# A gap between words longer than SILENCE_THRESHOLD seconds is a candidate cut.
# Shorter gaps are natural speech rhythm and are always kept.
SILENCE_THRESHOLD = 0.6
# When trimming a long pause we keep KEEP_PAD seconds of silence on each side so
# speech doesn't sound spliced — preserves natural breathing, avoids the
# over-sterilized "machine-gun jump cut" feel.
KEEP_PAD = 0.15
# Don't bother cutting anything shorter than this (not worth a splice).
MIN_CUT = 0.12

# ── Filler vocabulary ─────────────────────────────────────────────────────────
# Non-lexical fillers — almost never meaningful, safe to remove by default.
FILLER_WORDS = {
    "um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mmm", "mhm",
}
# Discourse markers — sometimes filler, sometimes meaningful ("I literally
# can't"). Off by default to preserve voice; enable per-project if desired.
DISCOURSE_FILLERS = {
    "like", "basically", "literally", "actually", "honestly", "right",
}
FILLER_PHRASES = [
    ("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of"),
]
REMOVE_DISCOURSE_FILLERS = False
REMOVE_FILLER_PHRASES = False

# ── Render ────────────────────────────────────────────────────────────────────
VIDEO_CODEC = "libx264"
VIDEO_PRESET = "veryfast"
VIDEO_CRF = 20
AUDIO_CODEC = "aac"
AUDIO_BITRATE_OUT = "192k"
# EBU R128 loudness normalization target (YouTube/podcast-friendly).
NORMALIZE_AUDIO = True
LOUDNORM_I = -16.0
LOUDNORM_TP = -1.5
LOUDNORM_LRA = 11.0

# ── Captions ──────────────────────────────────────────────────────────────────
CAPTION_FONT = "DejaVu Sans"
CAPTION_COLOR = "#D4A017"     # deep autumn yellow (matches existing look)
CAPTION_OUTLINE = "#000000"
# FontSize and vertical margin scale with video height (see captions.py).
CAPTION_FONTSIZE_RATIO = 0.045
CAPTION_OUTLINE_WIDTH = 3
CAPTION_BOTTOM_MARGIN_RATIO = 0.22   # caption sits ~78% down the frame
CAPTION_UPPERCASE = True
