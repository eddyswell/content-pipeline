import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import config


SLIDE_W = config.SLIDE_WIDTH
SLIDE_H = config.SLIDE_HEIGHT
PADDING = 80
TEXT_AREA_W = SLIDE_W - (PADDING * 2)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        config.TIKTOK_FONT_PATH if not bold else config.TIKTOK_FONT_BOLD_PATH,
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _crop_to_fill(img: Image.Image) -> Image.Image:
    target_ratio = SLIDE_W / SLIDE_H
    img_ratio = img.width / img.height
    if img_ratio > target_ratio:
        new_h = SLIDE_H
        new_w = int(img_ratio * SLIDE_H)
    else:
        new_w = SLIDE_W
        new_h = int(SLIDE_W / img_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - SLIDE_W) // 2
    top = (new_h - SLIDE_H) // 2
    return img.crop((left, top, left + SLIDE_W, top + SLIDE_H))


def _add_overlay(img: Image.Image, opacity: int) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, opacity))
    base = img.convert("RGBA")
    return Image.alpha_composite(base, overlay).convert("RGB")


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_centered_text(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont,
                         y_start: int, color: tuple, line_spacing: int = 12) -> int:
    y = y_start
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (SLIDE_W - w) // 2
        # subtle shadow for readability
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font, fill=color)
        y += (bbox[3] - bbox[1]) + line_spacing
    return y


def _make_hook_slide(bg: Image.Image, hook_text: str, creator: dict, slide_num: int, total: int) -> Image.Image:
    img = _add_overlay(bg, creator["overlay_opacity"])
    draw = ImageDraw.Draw(img, "RGBA")

    accent = creator["accent_color"]

    # Slide counter (top right)
    counter_font = _load_font(36)
    counter = f"{slide_num}/{total}"
    bbox = draw.textbbox((0, 0), counter, font=counter_font)
    draw.text((SLIDE_W - PADDING - (bbox[2] - bbox[0]), PADDING), counter, font=counter_font, fill=(200, 200, 200))

    # Hook text — large, centered vertically
    hook_font = _load_font(72, bold=True)
    lines = _wrap_text(draw, hook_text, hook_font, TEXT_AREA_W)
    line_h = hook_font.size + 16
    total_h = len(lines) * line_h
    y_start = (SLIDE_H - total_h) // 2 - 60
    _draw_centered_text(draw, lines, hook_font, y_start, (255, 255, 255), line_spacing=16)

    # Accent line below hook
    line_y = y_start + total_h + 30
    line_x = (SLIDE_W - 120) // 2
    draw.rectangle([line_x, line_y, line_x + 120, line_y + 4], fill=accent)

    # Handle at bottom
    handle_font = _load_font(38)
    handle = creator["handle"]
    bbox = draw.textbbox((0, 0), handle, font=handle_font)
    hw = bbox[2] - bbox[0]
    draw.text(((SLIDE_W - hw) // 2, SLIDE_H - PADDING - 50), handle, font=handle_font, fill=(*accent, 220))

    return img


def _make_content_slide(bg: Image.Image, slide: dict, creator: dict, slide_num: int, total: int) -> Image.Image:
    img = _add_overlay(bg, creator["overlay_opacity"] + 20)
    draw = ImageDraw.Draw(img, "RGBA")

    accent = creator["accent_color"]

    # Slide counter
    counter_font = _load_font(36)
    counter = f"{slide_num}/{total}"
    bbox = draw.textbbox((0, 0), counter, font=counter_font)
    draw.text((SLIDE_W - PADDING - (bbox[2] - bbox[0]), PADDING), counter, font=counter_font, fill=(200, 200, 200))

    # Number badge
    num_font = _load_font(52, bold=True)
    num_text = f"#{slide['number']}"
    bbox = draw.textbbox((0, 0), num_text, font=num_font)
    nw = bbox[2] - bbox[0]
    draw.text(((SLIDE_W - nw) // 2, SLIDE_H // 2 - 280), num_text, font=num_font, fill=(*accent, 255))

    # Title
    title_font = _load_font(68, bold=True)
    title_lines = _wrap_text(draw, slide["title"].upper(), title_font, TEXT_AREA_W)
    y = _draw_centered_text(draw, title_lines, title_font, SLIDE_H // 2 - 190, (255, 255, 255), line_spacing=14)

    # Divider
    y += 24
    lx = (SLIDE_W - 80) // 2
    draw.rectangle([lx, y, lx + 80, y + 3], fill=accent)
    y += 36

    # Body text
    body_font = _load_font(48)
    body_lines = _wrap_text(draw, slide["body"], body_font, TEXT_AREA_W)
    _draw_centered_text(draw, body_lines, body_font, y, (230, 230, 230), line_spacing=12)

    # Handle
    handle_font = _load_font(36)
    handle = creator["handle"]
    bbox = draw.textbbox((0, 0), handle, font=handle_font)
    hw = bbox[2] - bbox[0]
    draw.text(((SLIDE_W - hw) // 2, SLIDE_H - PADDING - 50), handle, font=handle_font, fill=(*accent, 180))

    return img


def _make_cta_slide(bg: Image.Image, cta_text: str, creator: dict, slide_num: int, total: int) -> Image.Image:
    img = _add_overlay(bg, creator["overlay_opacity"])
    draw = ImageDraw.Draw(img, "RGBA")

    accent = creator["accent_color"]

    # Slide counter
    counter_font = _load_font(36)
    counter = f"{slide_num}/{total}"
    bbox = draw.textbbox((0, 0), counter, font=counter_font)
    draw.text((SLIDE_W - PADDING - (bbox[2] - bbox[0]), PADDING), counter, font=counter_font, fill=(200, 200, 200))

    # CTA text
    cta_font = _load_font(64, bold=True)
    lines = _wrap_text(draw, cta_text, cta_font, TEXT_AREA_W)
    line_h = cta_font.size + 18
    total_h = len(lines) * line_h
    y_start = (SLIDE_H - total_h) // 2 - 40
    _draw_centered_text(draw, lines, cta_font, y_start, (255, 255, 255), line_spacing=18)

    # Follow prompt
    follow_font = _load_font(44)
    follow_text = f"Follow {creator['handle']} for more"
    lines2 = _wrap_text(draw, follow_text, follow_font, TEXT_AREA_W)
    y_follow = y_start + total_h + 60
    _draw_centered_text(draw, lines2, follow_font, y_follow, (*accent, 220), line_spacing=10)

    return img


def generate_slideshow(content: dict, creator_key: str, output_dir: str) -> list[str]:
    creator = config.TIKTOK_CREATORS[creator_key]
    images_dir = Path(config.TIKTOK_INPUT_IMAGES_DIR)
    image_files = sorted(
        [f for f in images_dir.iterdir() if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    )

    if not image_files:
        print(f"ERROR: No images found in {images_dir}/")
        print("Drop your Pinterest images there and re-run.")
        sys.exit(1)

    slides_data = content["slides"]
    # total slides = hook + content + cta
    total_slides = 1 + len(slides_data) + 1

    # Cycle images if we have fewer than slides
    def get_bg(idx: int) -> Image.Image:
        path = image_files[idx % len(image_files)]
        return _crop_to_fill(Image.open(path).convert("RGB"))

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    generated = []
    slide_idx = 0

    # Slide 1: hook
    img = _make_hook_slide(get_bg(slide_idx), content["hook"], creator, 1, total_slides)
    path = out_path / "slide_01.png"
    img.save(path, "PNG")
    generated.append(str(path))
    slide_idx += 1

    # Content slides
    for i, slide in enumerate(slides_data, start=1):
        img = _make_content_slide(get_bg(slide_idx), slide, creator, i + 1, total_slides)
        path = out_path / f"slide_{i + 1:02d}.png"
        img.save(path, "PNG")
        generated.append(str(path))
        slide_idx += 1

    # CTA slide
    img = _make_cta_slide(get_bg(slide_idx), content["cta"], creator, total_slides, total_slides)
    path = out_path / f"slide_{total_slides:02d}.png"
    img.save(path, "PNG")
    generated.append(str(path))

    return generated
