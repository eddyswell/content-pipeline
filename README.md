# Hook Video Pipeline

Automated pipeline for generating talking-head Instagram Reels from text hooks.

## What it does

1. **Voice** — Reads hooks from `input/hooks.txt`, generates MP3s via ElevenLabs
2. **Video** — Animates a character image with each voice via HeyGen (9:16 format)
3. **Captions** — Transcribes audio via Whisper, burns word-by-word captions via FFmpeg

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Install FFmpeg (required for captions)

```bash
brew install ffmpeg   # macOS
# or
sudo apt install ffmpeg  # Ubuntu/Debian
```

### 3. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `ELEVENLABS_API_KEY` — from elevenlabs.io
- `HEYGEN_API_KEY` — from heygen.com
- `OPENAI_API_KEY` — for Whisper transcription
- `ANTHROPIC_API_KEY` — for hook generation (optional)

### 4. Configure voice

Edit `config.py` and set `ELEVENLABS_VOICE_ID` to your ElevenLabs voice ID.

### 5. Add assets

- Drop character images (`.jpg` or `.png`) into `assets/images/`
- Add hooks to `input/hooks.txt` (one per line; lines starting with `#` are skipped)

## Usage

### Full pipeline

```bash
python pipeline.py
```

### Run specific steps

```bash
python pipeline.py --steps voices
python pipeline.py --steps voices,videos
python pipeline.py --steps captions
```

### Run steps individually

```bash
python generate_voices.py           # generate all voice files
python generate_voices.py --test    # test with a single line
python animate_batch.py             # generate all videos
python add_captions.py              # burn captions into all videos
```

### Generate hooks with Claude

```bash
python hook_generator.py "confidence and self-improvement"
python hook_generator.py "dating advice" --count 20
python hook_generator.py "fitness motivation" --append   # add to existing hooks.txt
```

## Output structure

```
output/
  voices/     voice_01.mp3, voice_02.mp3 ...  (+ .srt transcript files)
  videos/     video_01.mp4, video_02.mp4 ...  (raw animated videos)
  final/      final_01.mp4, final_02.mp4 ...  (captioned, ready to post)
```

## Tips

- All steps are idempotent — re-running skips already-completed files
- Test your ElevenLabs setup before a full batch: `python generate_voices.py --test`
- The pipeline asks before continuing past any failed step
- Up to 3 HeyGen video jobs run concurrently
- Caption timing uses word-level Whisper timestamps for precise sync

---

# Editing Assistant (`editor/`)

A separate pipeline that **cleans up your own talking-head footage** instead of
generating synthetic clips. It removes fillers and dead air, tightens pacing,
and burns captions — while preserving your natural delivery. It is an
*assistant*: it proposes a cut map you review before anything renders.

**Layers:** transcribe → annotate → cut map → *(human review)* → render → captions

The **cut map** (`cutmap.json`) is the contract. Detection only *proposes*
keep/cut segments; you can flip any `action` or add `"protect": true` to shield
an emphasis/comedic pause. `render` only ever executes what the map says.

### Requirements

- `ffmpeg` + `ffprobe` on PATH
- `OPENAI_API_KEY` in `.env` (Whisper transcription)

### Usage

```bash
# Transcribe + build a proposed cut map, then stop for review
python -m editor run path/to/video.mp4 --name my_clip

# Inspect / hand-edit projects/my_clip/cutmap.json, then re-check
python -m editor review my_clip

# Render the edit + burn captions → projects/my_clip/out/
python -m editor render my_clip

# Skip review and render in one shot (quick tests)
python -m editor run path/to/video.mp4 --name my_clip --render
```

Tunables (filler list, pause threshold, breathing pad, caption style) live in
`editor/config.py`. Discourse-marker fillers ("like", "you know") are **off by
default** to protect your voice — enable them per taste.

### What's automated vs. deferred

- **Automated now (deterministic):** filler removal, silence/dead-air trimming
  with breathing pads, frame-accurate cuts, loudness normalization, word-level
  captions remapped onto the edited timeline.
- **Deferred (taste / packaging layers):** LLM pause classification
  (dead air vs. dramatic), repeated-phrase tightening, hook/title generation,
  vidIQ packaging, zoom-on-emphasis, reframing. These bolt onto the same cut
  map without restructuring.
