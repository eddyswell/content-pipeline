"""
tiktok_analyzer.py — Analyze a downloaded TikTok slideshow with Claude Opus 4.7.

Extracts: hook, why it works, hook structure, 5 variations, Pinterest search queries.
Saves results to SQLite and appends hook variations to input/tiktok_hooks.txt.

Usage:
    python tiktok_analyzer.py <post_id>            # analyze already-downloaded post
    python tiktok_analyzer.py --url <tiktok_url>   # download + analyze in one go
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from PIL import Image

import config
from tiktok_downloader import init_db, get_post, save_hooks, download

load_dotenv()

NICHE     = "personal finance for women in their 20s and 30s"
HOOKS_OUT = Path(config.TIKTOK_HOOKS_FILE)
MAX_SLIDES_TO_SEND = 6   # Claude handles up to 20; cap at 6 for cost/speed


# ── Image encoding ────────────────────────────────────────────────────────────

def _encode_image(path: Path, max_long_side: int = 1024) -> tuple[str, str]:
    """Resize to ≤max_long_side and return (base64_data, media_type)."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_long_side:
            scale = max_long_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"


def _image_block(path: Path) -> dict:
    b64, mt = _encode_image(path)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mt, "data": b64},
    }


# ── Claude analysis ───────────────────────────────────────────────────────────

SYSTEM = (
    f"You are a short-form content strategist specialising in viral TikTok slideshows "
    f"for the niche: {NICHE}. "
    "You analyse real viral slideshows and extract what makes them work, "
    "then generate proven hook variations. "
    "Return ONLY valid JSON — no markdown, no explanation."
)

PROMPT = f"""Analyse these TikTok slideshow images and return a JSON object with this exact structure:

{{
  "hook": "the exact hook/headline from the first slide",
  "hook_type": "one of: curiosity | pain_point | surprise | relatability | fomo",
  "why_it_works": "1-2 sentences explaining the psychological trigger",
  "hook_structure": "the reusable formula, e.g. 'number + negative outcome + identity target'",
  "variations": [
    "hook variation 1 — max 10 words",
    "hook variation 2 — max 10 words",
    "hook variation 3 — max 10 words",
    "hook variation 4 — max 10 words",
    "hook variation 5 — max 10 words"
  ],
  "pinterest_queries": [
    "query 1 — 2-4 words, matches the visual aesthetic of this slideshow",
    "query 2",
    "query 3",
    "query 4",
    "query 5"
  ]
}}

Rules for variations:
- Niche: {NICHE}
- Each variation triggers one of: curiosity / FOMO / empathy
- Max 10 words each
- Mix questions and strong statements
- No generic openers like "Did you know"
- Write as if the creator is speaking in first person

Rules for pinterest_queries:
- Focus on the visual style and aesthetic, not the topic
- E.g. "dark moody mirror selfie", "clean minimal bedroom aesthetic", "golden hour outdoor portrait"
"""


def analyze(post_id: str) -> dict | None:
    init_db()

    post = get_post(post_id)
    if not post:
        print(f"ERROR: post_id '{post_id}' not found in DB. Download it first.")
        return None

    image_paths_raw = post["image_paths"]
    if isinstance(image_paths_raw, str):
        image_paths = json.loads(image_paths_raw)
    else:
        image_paths = image_paths_raw

    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        print(f"ERROR: No local images found for post {post_id}.")
        return None

    # Cap at MAX_SLIDES_TO_SEND (usually slide 1 is all we need; send up to 6)
    paths = paths[:MAX_SLIDES_TO_SEND]

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        return None

    print(f"\nAnalyzing {len(paths)} slide(s) with Claude Opus 4.7...")
    print(f"  Post   : {post['url']}")
    print(f"  Caption: {post['caption'][:80]}...")

    content_blocks = [_image_block(p) for p in paths]
    content_blocks.append({"type": "text", "text": PROMPT})

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=config.TIKTOK_MODEL,
        max_tokens=1536,
        system=SYSTEM,
        messages=[{"role": "user", "content": content_blocks}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Could not parse Claude's response as JSON: {e}")
        print("Raw response:", raw[:500])
        return None

    # Save to DB
    save_hooks(post_id, analysis, niche=NICHE)

    # Append hook variations to the hooks file
    HOOKS_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(HOOKS_OUT, "a", encoding="utf-8") as f:
        f.write(f"\n# Extracted from {post['url']}\n")
        for v in analysis.get("variations", []):
            f.write(v.strip() + "\n")

    return analysis


def print_analysis(analysis: dict):
    print("\n" + "=" * 54)
    print("  HOOK ANALYSIS")
    print("=" * 54)
    print(f"  Hook       : {analysis.get('hook', '')}")
    print(f"  Type       : {analysis.get('hook_type', '')}")
    print(f"  Why works  : {analysis.get('why_it_works', '')}")
    print(f"  Structure  : {analysis.get('hook_structure', '')}")
    print("\n  Variations:")
    for i, v in enumerate(analysis.get("variations", []), 1):
        print(f"    {i}. {v}")
    print("\n  Pinterest queries:")
    for q in analysis.get("pinterest_queries", []):
        print(f"    • {q}")
    print("=" * 54)
    print(f"\n  ✓ Hooks saved to {HOOKS_OUT}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Analyze a TikTok slideshow with Claude")
    parser.add_argument("post_id",  nargs="?", help="post_id from DB")
    parser.add_argument("--url",    help="TikTok URL (downloads first if not in DB)")
    args = parser.parse_args()

    if args.url:
        rec = download(args.url)
        if not rec:
            sys.exit(1)
        post_id = rec["post_id"]
    elif args.post_id:
        post_id = args.post_id
    else:
        print("Usage: python tiktok_analyzer.py <post_id>  OR  --url <tiktok_url>")
        sys.exit(1)

    result = analyze(post_id)
    if result:
        print_analysis(result)
