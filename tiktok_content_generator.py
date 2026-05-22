import os
import json
import sys
from dotenv import load_dotenv
import anthropic
import config

load_dotenv()


def generate_slide_content(hook: str, creator_key: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    creator = config.TIKTOK_CREATORS[creator_key]
    age = creator["age"]
    tone = creator["tone"]

    system = (
        f"You are writing TikTok slideshow content for a {age}-year-old creator: {tone}. "
        "Write in first person. Be punchy, direct, and relatable. No fluff. No filler words. "
        "You must return ONLY valid JSON — no markdown, no explanation, no code fences."
    )

    prompt = f"""The first slide hook is: "{hook}"

Generate a complete TikTok personal finance slideshow with exactly 5 content slides that follow through on this hook.

Rules:
- Each slide covers ONE clear point. Short and punchy.
- First person voice, written as the creator.
- Title: 2–4 words max
- Body: 1–2 sentences, max 20 words total
- CTA: max 10 words (e.g. "Save this. Your future self will thank you.")

Return ONLY this JSON structure:
{{
  "hook": "{hook}",
  "slides": [
    {{"number": 1, "title": "...", "body": "..."}},
    {{"number": 2, "title": "...", "body": "..."}},
    {{"number": 3, "title": "...", "body": "..."}},
    {{"number": 4, "title": "...", "body": "..."}},
    {{"number": 5, "title": "...", "body": "..."}}
  ],
  "cta": "..."
}}"""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=config.TIKTOK_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Strip markdown fences if model added them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
