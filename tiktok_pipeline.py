"""
TikTok Slideshow Pipeline

Usage:
    python tiktok_pipeline.py                              # interactive
    python tiktok_pipeline.py --creator "Fiona (US)" --hook "6 toxic money habits"
    python tiktok_pipeline.py --batch input/tiktok_hooks.txt
    python tiktok_pipeline.py --generate-photos            # run face-swap first, then slides
    python tiktok_pipeline.py --photos-only                # just generate Fiona photos, no slides
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import config
from tiktok_content_generator import generate_slide_content
from generate_slides import generate_slideshow


SAMPLE_HOOKS = [
    "Just in case your parents didn't teach you about money",
    "6 toxic money habits you NEED to break",
    "Money mistakes I made in my 20s (so you don't have to)",
    "If you have $400/month to invest, here's how to turn it into $2.2M",
    "5 things I stopped buying and saved SO much money",
    "How I got my financial life together as a 24 year old",
    "Rules rich people don't talk about",
    "My non-negotiables for spending money",
    "Your 12-month savings plan (starting from zero)",
    "How I doubled my savings by fixing one habit",
]

INSPIRATION_DIR = Path("input/inspiration")    # Pinterest reference images go here
GENERATED_PHOTOS_DIR = Path("input/images")    # Face-swapped Fiona photos end up here


# ── Face-swap step ────────────────────────────────────────────────────────────

def generate_creator_photos(creator_key: str) -> list[Path]:
    """
    Run Nano Banana 2 face-swap on every image in input/inspiration/.
    Outputs go to input/images/ so generate_slides.py picks them up automatically.
    Skips images that already have a matching output (idempotent).
    """
    from generate_images import generate_batch_parallel, get_collage_path
    from generate_images import fill_prompt_with_claude, _load_master_prompt

    inspiration = sorted(
        f for f in INSPIRATION_DIR.iterdir()
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not inspiration:
        print(f"\nERROR: No inspiration images in {INSPIRATION_DIR}/")
        print("Drop 5-10 Pinterest lifestyle images there and re-run.\n")
        return []

    GENERATED_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    collage = get_collage_path(creator_key)
    master  = _load_master_prompt()
    api_key = os.getenv("WAVESPEED_API_KEY", "")
    if not api_key:
        print("ERROR: WAVESPEED_API_KEY not set in .env")
        return []

    print(f"\nGenerating {len(inspiration)} creator photos via Nano Banana 2...")
    print(f"  Creator  : {creator_key}")
    print(f"  Collage  : {collage}")
    print(f"  Output   : {GENERATED_PHOTOS_DIR}/\n")

    # Build prompts for each reference image
    prompts = []
    for ref in inspiration:
        print(f"  Filling prompt for {ref.name}...")
        prompts.append(fill_prompt_with_claude(collage, ref, master))

    out_paths = [GENERATED_PHOTOS_DIR / f"photo_{i:02d}.jpg" for i in range(len(inspiration))]
    indices   = list(range(len(inspiration)))

    results = generate_batch_parallel(
        ref_paths      = inspiration,
        collage_path   = collage,
        prompts        = prompts,
        api_key        = api_key,
        max_concurrent = 3,
        out_paths      = out_paths,
        indices        = indices,
    )

    succeeded = [r.out_path for r in results if r.status == "success" and r.out_path]
    failed    = [r for r in results if r.status != "success"]

    print(f"\n✓ {len(succeeded)}/{len(inspiration)} photos generated → {GENERATED_PHOTOS_DIR}/")
    if failed:
        print(f"  ⚠ {len(failed)} failed:")
        for r in failed:
            print(f"    {r.ref_path.name}: {r.error[:80]}")

    return succeeded


# ── Slide generation step ─────────────────────────────────────────────────────

def pick_creator() -> str:
    creators = list(config.TIKTOK_CREATORS.keys())
    print("\nChoose a creator:")
    for i, name in enumerate(creators, 1):
        c = config.TIKTOK_CREATORS[name]
        print(f"  {i}. {name}  —  age {c['age']}, {c['handle']}")
    while True:
        choice = input("\nEnter number (or name): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(creators):
            return creators[int(choice) - 1]
        if choice in config.TIKTOK_CREATORS:
            return choice
        print("Invalid choice, try again.")


def pick_hook() -> str:
    print("\nChoose a hook or enter your own:")
    for i, hook in enumerate(SAMPLE_HOOKS, 1):
        print(f"  {i:2d}. {hook}")
    print(f"  {len(SAMPLE_HOOKS) + 1:2d}. Enter custom hook")
    while True:
        choice = input("\nEnter number or type your hook directly: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(SAMPLE_HOOKS):
                return SAMPLE_HOOKS[idx - 1]
            if idx == len(SAMPLE_HOOKS) + 1:
                return input("Your hook: ").strip()
        elif choice:
            return choice
        print("Please enter a valid choice.")


def run_single(creator_key: str, hook: str, batch_name: str = "") -> str:
    print(f"\n{'='*54}")
    print(f"  Creator : {creator_key}")
    print(f"  Hook    : {hook}")
    print(f"{'='*54}")

    print(f"\nGenerating slide content with Claude Opus 4.7...")
    content = generate_slide_content(hook, creator_key)

    print("\n--- Slide Preview ---")
    print(f"  [1] HOOK  : {content['hook']}")
    for slide in content["slides"]:
        print(f"  [{slide['number']+1}] #{slide['number']:02d} {slide['title'].upper()}  —  {slide['body']}")
    print(f"  [{len(content['slides'])+2}] CTA   : {content['cta']}")
    print("---------------------")

    answer = input("\nGenerate slides with these? (y/n): ").strip().lower()
    if answer != "y":
        print("Skipped.")
        return ""

    timestamp = batch_name or time.strftime("%Y%m%d_%H%M%S")
    safe_hook = hook[:40].replace(" ", "_").replace("/", "-")
    out_dir = f"{config.TIKTOK_OUTPUT_DIR}/{timestamp}_{safe_hook}"

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(f"{out_dir}/content.json", "w") as f:
        json.dump({"creator": creator_key, "content": content}, f, indent=2)

    print("\nRendering PNG slides...")
    paths = generate_slideshow(content, creator_key, out_dir)

    print(f"\n✓ {len(paths)} slides → {out_dir}/")
    for p in paths:
        print(f"    {p}")

    return out_dir


def run_batch(creator_key: str, hooks_file: str):
    hooks = [
        line.strip()
        for line in Path(hooks_file).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"\nBatch mode: {len(hooks)} hooks from {hooks_file}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    for i, hook in enumerate(hooks, 1):
        print(f"\n[{i}/{len(hooks)}]")
        run_single(creator_key, hook, batch_name=f"{ts}_batch{i:02d}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TikTok Slideshow Pipeline")
    parser.add_argument("--creator",          help="Creator name (e.g. 'Fiona (US)')")
    parser.add_argument("--hook",             help="Single hook text")
    parser.add_argument("--batch",            help="Path to .txt file with one hook per line")
    parser.add_argument("--generate-photos",  action="store_true",
                        help="Run face-swap on input/inspiration/ before generating slides")
    parser.add_argument("--photos-only",      action="store_true",
                        help="Only run face-swap, skip slide generation")
    args = parser.parse_args()

    print("=== TikTok Slideshow Pipeline ===")

    creator_key = args.creator if args.creator in config.TIKTOK_CREATORS else pick_creator()

    # Optional: generate Fiona photos first
    if args.generate_photos or args.photos_only:
        photos = generate_creator_photos(creator_key)
        if not photos:
            print("Photo generation failed or produced no output. Aborting.")
            return
        if args.photos_only:
            return

    # Slide generation
    if args.batch:
        run_batch(creator_key, args.batch)
    elif args.hook:
        run_single(creator_key, args.hook)
    else:
        hook = pick_hook()
        run_single(creator_key, hook)


if __name__ == "__main__":
    main()
