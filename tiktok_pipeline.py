"""
TikTok Slideshow Pipeline

Usage:
    python tiktok_pipeline.py
    python tiktok_pipeline.py --creator "Fiona (US)" --hook "6 toxic money habits you need to break"
    python tiktok_pipeline.py --batch hooks.txt  (one hook per line, generates a slideshow per hook)
"""

import argparse
import json
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
        if choice in creators:
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
    print(f"\n{'='*50}")
    print(f"Creator : {creator_key}")
    print(f"Hook    : {hook}")
    print(f"{'='*50}")

    print("\nGenerating slide content with Claude Opus 4.7...")
    content = generate_slide_content(hook, creator_key)

    print("\n--- Slide Preview ---")
    print(f"[1] HOOK  : {content['hook']}")
    for slide in content["slides"]:
        print(f"[{slide['number']+1}] #{slide['number']} {slide['title'].upper()}  —  {slide['body']}")
    print(f"[{len(content['slides'])+2}] CTA   : {content['cta']}")
    print("---------------------")

    answer = input("\nGenerate slides with these? (y/n): ").strip().lower()
    if answer != "y":
        print("Skipped.")
        return ""

    timestamp = batch_name or time.strftime("%Y%m%d_%H%M%S")
    safe_hook = hook[:40].replace(" ", "_").replace("/", "-")
    out_dir = f"{config.TIKTOK_OUTPUT_DIR}/{timestamp}_{safe_hook}"

    # Save content JSON alongside slides
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(f"{out_dir}/content.json", "w") as f:
        json.dump({"creator": creator_key, "content": content}, f, indent=2)

    print("\nGenerating PNG slides...")
    paths = generate_slideshow(content, creator_key, out_dir)

    print(f"\n✓ {len(paths)} slides saved to: {out_dir}/")
    for p in paths:
        print(f"  {p}")

    return out_dir


def run_batch(creator_key: str, hooks_file: str):
    hooks = [
        line.strip()
        for line in Path(hooks_file).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"\nBatch mode: {len(hooks)} hooks from {hooks_file}")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    for i, hook in enumerate(hooks, 1):
        print(f"\n[{i}/{len(hooks)}]")
        run_single(creator_key, hook, batch_name=f"{timestamp}_batch{i:02d}")


def main():
    parser = argparse.ArgumentParser(description="TikTok Slideshow Pipeline")
    parser.add_argument("--creator", help="Creator name (e.g. 'Fiona (US)')")
    parser.add_argument("--hook", help="Hook text for a single slideshow")
    parser.add_argument("--batch", help="Path to a .txt file with one hook per line")
    args = parser.parse_args()

    print("=== TikTok Slideshow Pipeline ===")

    creator_key = args.creator if args.creator in config.TIKTOK_CREATORS else pick_creator()

    if args.batch:
        run_batch(creator_key, args.batch)
    elif args.hook:
        run_single(creator_key, args.hook)
    else:
        hook = pick_hook()
        run_single(creator_key, hook)


if __name__ == "__main__":
    main()
