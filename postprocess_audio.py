"""
postprocess_audio.py — make ElevenLabs voice feel natural.

Fixes the "talking in a box" effect by:
  1. Adding a subtle pink-noise room-tone layer (~8 % of voice level)
  2. Simulating a tiny early reflection (simple echo at -22 dB, 22 ms delay)

No external CLI tools required — pure Python via pydub + numpy + scipy.

Requirements: pydub, numpy, scipy  (ffmpeg must be available for mp3 I/O)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.signal import lfilter
from pydub import AudioSegment


# ── Pink-noise generation ─────────────────────────────────────────────────────

_PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
_PINK_A = np.array([1.0, -2.494956002,  2.017265875, -0.522189400])


def _pink_noise(n_samples: int) -> np.ndarray:
    """Return unit-amplitude pink noise array (float64)."""
    white = np.random.default_rng().standard_normal(n_samples)
    pink  = lfilter(_PINK_B, _PINK_A, white)
    peak  = np.max(np.abs(pink)) or 1.0
    return pink / peak


def _array_to_segment(arr: np.ndarray, frame_rate: int, channels: int) -> AudioSegment:
    """Convert a float64 [-1, 1] array to a pydub AudioSegment."""
    pcm = np.clip(arr * 32767, -32768, 32767).astype(np.int16)
    if channels == 2:
        pcm = np.column_stack([pcm, pcm])   # mono → stereo
    return AudioSegment(
        pcm.tobytes(),
        frame_rate=frame_rate,
        sample_width=2,
        channels=channels,
    )


# ── Early reflection (pseudo-reverb) ─────────────────────────────────────────

def _early_reflection(seg: AudioSegment, delay_ms: int = 22, decay_db: float = -22.0) -> AudioSegment:
    """
    Overlay a single early reflection at `delay_ms` ms with `decay_db` attenuation.
    Gives a subtle sense of physical space without audible echo.
    """
    reflection = AudioSegment.silent(duration=delay_ms) + seg
    reflection = reflection[: len(seg)]          # trim to original length
    reflection = reflection.apply_gain(decay_db)
    return seg.overlay(reflection)


# ── Public entry point ────────────────────────────────────────────────────────

def process(mp3_path: Path, ambient_db_below_voice: float = -18.0) -> Path:
    """
    Post-process a voice MP3 file in place.

    Args:
        mp3_path:              Path to the .mp3 file (overwritten on completion).
        ambient_db_below_voice: How many dB quieter the room tone is vs. the voice.
                               -18 dB ≈ 8 % amplitude — subtle but effective.

    Returns:
        The same path (file overwritten with processed audio).
    """
    voice = AudioSegment.from_mp3(mp3_path)

    sr       = voice.frame_rate
    channels = voice.channels
    n        = int(sr * len(voice) / 1000)       # total samples

    # 1. Generate pink-noise room tone
    noise_arr = _pink_noise(n)
    noise_seg = _array_to_segment(noise_arr, sr, channels)
    noise_seg = noise_seg.set_frame_rate(sr).set_channels(channels)

    # Scale noise to sit `ambient_db_below_voice` dB below the voice peak
    target_dbfs = voice.dBFS + ambient_db_below_voice
    noise_seg   = noise_seg.apply_gain(target_dbfs - noise_seg.dBFS)

    # 2. Mix voice + room tone
    mixed = voice.overlay(noise_seg)

    # 3. Add a subtle early reflection for room character
    mixed = _early_reflection(mixed, delay_ms=22, decay_db=-22.0)

    # 4. Export (overwrite original)
    mixed.export(mp3_path, format="mp3", bitrate="192k")
    return mp3_path


def process_all(voices_dir: Path = Path("output/voices")) -> int:
    """Process all voice_*.mp3 files in `voices_dir`. Returns count processed."""
    files = sorted(voices_dir.glob("voice_*.mp3"))
    for f in files:
        process(f)
    return len(files)
