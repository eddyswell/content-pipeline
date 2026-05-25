"""
generate_slides.py — TikTok-native slide renderer.

Design philosophy: looks like the creator took a photo and typed text
directly in the TikTok app. Full-bleed photo, bold white text with
black stroke outline, no overlay boxes, no dark vignette.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import config

SLIDE_W = config.SLIDE_WIDTH
SLIDE_H = config.SLIDE_HEIGHT
PADDING = 72
TEXT_AREA_W = SLIDE_W - (PADDING * 2)


# ── Font loading ──────────────────────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """
    Try fonts in priority order. First found wins.
    Drop Montserrat-Black.ttf into assets/fonts/ for the best look.
    """
    candidates = [
        config.TIKTOK_FONT_PATH,                                                  # Montserrat-Black (ideal)
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    # Absolute last resort — Pillow bitmap default (ugly but functional)
    return ImageFont.load_default()


# ── Text utilities ────────────────────────────────────────────────────────────

def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        w = draw.textbbox((0, 0), test, font=font)[2]
        if w > max_w and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    y_start: int,
    fill: tuple = (255, 255, 255),
    stroke: tuple = (0, 0, 0),
    stroke_width: int = 6,
    line_gap: int = 10,
    align: str = "center",   # "center" | "left"
) -> int:
    """Draw text with outline stroke. Returns y after last line."""
    y = y_start
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        if align == "center":
            x = (SLIDE_W - lw) // 2
        else:
            x = PADDING
        # Stroke (drawn first, 8 directions)
        for dx in range(-stroke_width, stroke_width + 1, 2):
            for dy in range(-stroke_width, stroke_width + 1, 2):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), line, font=font, fill=stroke)
        # Main text
        draw.text((x, y), line, font=font, fill=fill)
        y += lh + line_gap
    return y


def _subtle_gradient(img: Image.Image, strength: int = 80) -> Image.Image:
    """
    Add a very subtle dark gradient at top and bottom — just enough to help
    text read without killing the photo. strength=0 → skip entirely.
    """
    if strength == 0:
        return img
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fade_h = SLIDE_H // 4
    for i in range(fade_h):
        alpha_top = int(strength * (1 - i / fade_h))
        alpha_bot = int(strength * (1 - i / fade_h))
        draw.line([(0, i), (SLIDE_W, i)], fill=(0, 0, 0, alpha_top))
        draw.line([(0, SLIDE_H - 1 - i), (SLIDE_W, SLIDE_H - 1 - i)], fill=(0, 0, 0, alpha_bot))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# ── Image helpers ─────────────────────────────────────────────────────────────

def _crop_to_fill(img: Image.Image) -> Image.Image:
    target_ratio = SLIDE_W / SLIDE_H
    img_ratio = img.width / img.height
    if img_ratio > target_ratio:
        new_h, new_w = SLIDE_H, int(img_ratio * SLIDE_H)
    else:
        new_w, new_h = SLIDE_W, int(SLIDE_W / img_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - SLIDE_W) // 2
    top = (new_h - SLIDE_H) // 2
    return img.crop((left, top, left + SLIDE_W, top + SLIDE_H))


# ── Slide type renderers ──────────────────────────────────────────────────────

def _slide_hook(bg: Image.Image, hook: str, creator: dict, num: int, total: int) -> Image.Image:
    """Slide 1 — big hook text in the upper-center third."""
    img = _subtle_gradient(bg, strength=70)
    draw = ImageDraw.Draw(img)

    # Slide counter top-right
    small = _load_font(38)
    counter = f"{num} / {total}"
    cw = draw.textbbox((0, 0), counter, small)[2]
    _draw_outlined_text(draw, [counter], small, PADDING - 4,
                        fill=(255, 255, 255, 200), stroke=(0, 0, 0), stroke_width=4)

    # Hook text — large, upper third
    hook_font = _load_font(88)
    lines = _wrap(draw, hook, hook_font, TEXT_AREA_W)
    # Aim for upper third center
    line_h = hook_font.size + 14
    block_h = len(lines) * line_h
    y = max(PADDING + 60, SLIDE_H // 4 - block_h // 2)
    _draw_outlined_text(draw, lines, hook_font, y,
                        fill=(255, 255, 255), stroke=(0, 0, 0), stroke_width=7, line_gap=14)

    # Handle — bottom
    handle_font = _load_font(44)
    _draw_outlined_text(draw, [creator["handle"]], handle_font,
                        SLIDE_H - PADDING - 54,
                        fill=(*creator["accent_color"], 230),
                        stroke=(0, 0, 0), stroke_width=5)

    return img


def _slide_content(bg: Image.Image, slide: dict, creator: dict, num: int, total: int) -> Image.Image:
    """Content slides — number badge + title + body, mid-frame."""
    img = _subtle_gradient(bg, strength=60)
    draw = ImageDraw.Draw(img)

    accent = creator["accent_color"]

    # Slide counter
    small = _load_font(38)
    _draw_outlined_text(draw, [f"{num} / {total}"], small, PADDING - 4,
                        fill=(255, 255, 255), stroke=(0, 0, 0), stroke_width=4)

    # Number badge — e.g.  "01"
    num_font = _load_font(56)
    badge = f"{slide['number']:02d}"
    bw = draw.textbbox((0, 0), badge, num_font)[2]
    badge_x = (SLIDE_W - bw) // 2
    badge_y = SLIDE_H // 2 - 260
    _draw_outlined_text(draw, [badge], num_font, badge_y,
                        fill=accent, stroke=(0, 0, 0), stroke_width=5)

    # Title
    title_font = _load_font(82)
    title_lines = _wrap(draw, slide["title"].upper(), title_font, TEXT_AREA_W)
    title_h = len(title_lines) * (title_font.size + 12)
    y_title = SLIDE_H // 2 - 160
    y_after_title = _draw_outlined_text(
        draw, title_lines, title_font, y_title,
        fill=(255, 255, 255), stroke=(0, 0, 0), stroke_width=7, line_gap=12
    )

    # Body
    body_font = _load_font(52)
    body_lines = _wrap(draw, slide["body"], body_font, TEXT_AREA_W)
    _draw_outlined_text(
        draw, body_lines, body_font, y_after_title + 28,
        fill=(240, 240, 240), stroke=(0, 0, 0), stroke_width=5, line_gap=10
    )

    # Handle
    handle_font = _load_font(42)
    _draw_outlined_text(draw, [creator["handle"]], handle_font,
                        SLIDE_H - PADDING - 54,
                        fill=(*accent, 220),
                        stroke=(0, 0, 0), stroke_width=4)

    return img


def _slide_cta(bg: Image.Image, cta: str, creator: dict, num: int, total: int) -> Image.Image:
    """Last slide — CTA + follow prompt."""
    img = _subtle_gradient(bg, strength=75)
    draw = ImageDraw.Draw(img)

    accent = creator["accent_color"]

    # Slide counter
    small = _load_font(38)
    _draw_outlined_text(draw, [f"{num} / {total}"], small, PADDING - 4,
                        fill=(255, 255, 255), stroke=(0, 0, 0), stroke_width=4)

    # CTA text — centered vertically
    cta_font = _load_font(76)
    cta_lines = _wrap(draw, cta, cta_font, TEXT_AREA_W)
    block_h = len(cta_lines) * (cta_font.size + 16)
    y = SLIDE_H // 2 - block_h // 2 - 50
    y_after = _draw_outlined_text(
        draw, cta_lines, cta_font, y,
        fill=(255, 255, 255), stroke=(0, 0, 0), stroke_width=7, line_gap=16
    )

    # Follow line
    follow_font = _load_font(48)
    follow = f"Follow {creator['handle']} ✨"
    _draw_outlined_text(draw, [follow], follow_font, y_after + 50,
                        fill=accent, stroke=(0, 0, 0), stroke_width=5)

    return img


# ── Public entry point ────────────────────────────────────────────────────────

def generate_slideshow(content: dict, creator_key: str, output_dir: str) -> list[str]:
    creator = config.TIKTOK_CREATORS[creator_key]
    images_dir = Path(config.TIKTOK_INPUT_IMAGES_DIR)
    image_files = sorted(
        f for f in images_dir.iterdir()
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )

    if not image_files:
        print(f"\nERROR: No images found in {images_dir}/")
        print("Drop your Pinterest images (or generated Fiona photos) there and re-run.\n")
        sys.exit(1)

    slides_data = content["slides"]
    total_slides = 1 + len(slides_data) + 1  # hook + content + cta

    def get_bg(idx: int) -> Image.Image:
        return _crop_to_fill(Image.open(image_files[idx % len(image_files)]).convert("RGB"))

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []
    bg_idx = 0

    # Slide 1: hook
    img = _slide_hook(get_bg(bg_idx), content["hook"], creator, 1, total_slides)
    p = out_path / "slide_01.png"
    img.save(p, "PNG")
    generated.append(str(p))
    bg_idx += 1

    # Content slides
    for i, slide in enumerate(slides_data, start=1):
        img = _slide_content(get_bg(bg_idx), slide, creator, i + 1, total_slides)
        p = out_path / f"slide_{i + 1:02d}.png"
        img.save(p, "PNG")
        generated.append(str(p))
        bg_idx += 1

    # CTA slide
    img = _slide_cta(get_bg(bg_idx), content["cta"], creator, total_slides, total_slides)
    p = out_path / f"slide_{total_slides:02d}.png"
    img.save(p, "PNG")
    generated.append(str(p))

    return generated
