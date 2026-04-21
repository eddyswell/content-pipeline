import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
import anthropic
import config

load_dotenv()

SYSTEM_PROMPT = (
    "You are a short-form content strategist specializing in Instagram Reels hooks. "
    "Generate hooks that are bold, charismatic, and designed to stop mid-scroll. "
    "Each hook should be 1-2 sentences, deliverable as a talking-head clip, "
    "and suitable for Instagram's content policy. Output only the hooks, "
    "one per line, no numbering, no quotes."
)


def generate_hooks(topic: str, count: int) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Generate {count} Instagram Reels hooks about: {topic}",
            }
        ],
    )
    raw = message.content[0].text.strip()
    hooks = [line.strip() for line in raw.splitlines() if line.strip()]
    return hooks


def write_hooks(hooks: list[str], append: bool):
    out_path = Path(config.INPUT_HOOKS_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(out_path, mode) as f:
        if append and out_path.stat().st_size > 0:
            f.write("\n")
        f.write("\n".join(hooks) + "\n")
    action = "Appended" if append else "Wrote"
    print(f"\n{action} {len(hooks)} hooks to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate IG Reels hooks via Claude")
    parser.add_argument("topic", help="Topic or theme for the hooks")
    parser.add_argument("--append", action="store_true", help="Append to hooks.txt instead of overwriting")
    parser.add_argument("--count", type=int, default=10, help="Number of hooks to generate (default: 10)")
    args = parser.parse_args()

    print(f"Generating {args.count} hooks for: {args.topic}\n")
    hooks = generate_hooks(args.topic, args.count)

    print("--- Generated Hooks ---")
    for i, hook in enumerate(hooks, 1):
        print(f"{i}. {hook}")

    print("\n-----------------------")
    answer = input("Write these to hooks.txt? (y/n): ").strip().lower()
    if answer == "y":
        write_hooks(hooks, args.append)
    else:
        print("Aborted. No file written.")


if __name__ == "__main__":
    main()
