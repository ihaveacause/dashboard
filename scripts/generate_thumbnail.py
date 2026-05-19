"""
generate_thumbnail.py
Generates a YouTube thumbnail (1280x720) for an episode.

Usage:
  python generate_thumbnail.py \
    --bg_url      "https://xxx.supabase.co/storage/v1/object/public/channel-assets/module_default_bg.png" \
    --photo_url   "https://xxx.supabase.co/storage/v1/object/public/channel-assets/photo_tamil.jpg" \
    --logo_svg    "assets/ihaveacause_symbol.svg" \
    --episode_num 1 \
    --title       "நனவு என்றால் என்ன?" \
    --language    "tamil" \
    --output      "thumbnail_ep001.jpg"
"""

import argparse
import io
import os
import textwrap
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import cairosvg

# ── Constants ────────────────────────────────────────────────────────────────
W, H = 1280, 720
LOGO_SIZE    = 180          # logo symbol size (px)
PHOTO_SIZE   = 220          # person photo diameter (px)
PHOTO_MARGIN = 40           # from bottom-right corner

# Colours
OVERLAY_COLOUR  = (0, 0, 0, 120)        # dark overlay on bg for readability
BADGE_COLOUR    = (180, 0, 58, 220)     # deep pink badge
WHITE           = (255, 255, 255, 255)
SHADOW          = (0, 0, 0, 180)

# Font sizes
EPISODE_FONT_SIZE = 42
TITLE_FONT_SIZE   = 68
CHANNEL_FONT_SIZE = 28

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_image_from_url(url: str) -> Image.Image:
    with urllib.request.urlopen(url) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGBA")


def load_image_from_path(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def load_image(src: str) -> Image.Image:
    """Load from URL or local path."""
    if src.startswith("http"):
        return load_image_from_url(src)
    return load_image_from_path(src)


def svg_to_image(svg_path: str, size: int) -> Image.Image:
    """Convert SVG to PIL Image at given square size."""
    png_data = cairosvg.svg2png(
        url=svg_path,
        output_width=size,
        output_height=size,
    )
    return Image.open(io.BytesIO(png_data)).convert("RGBA")


def circle_crop(img: Image.Image, size: int) -> Image.Image:
    """Crop image to a circle of given diameter."""
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load Noto Sans for Tamil/Unicode support, fall back gracefully."""
    candidates = []
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_text_with_shadow(
    draw: ImageDraw.Draw,
    xy: tuple,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple = WHITE,
    shadow_offset: int = 3,
):
    x, y = xy
    # Shadow
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=SHADOW)
    # Main text
    draw.text((x, y), text, font=font, fill=fill)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current = ""
    dummy = Image.new("RGBA", (1, 1))
    draw  = ImageDraw.Draw(dummy)
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_thumbnail(
    bg_url: str,
    photo_url: str,
    logo_svg: str,
    episode_num: int,
    title: str,
    language: str,
    output: str,
):
    # 1. Background
    bg = load_image(bg_url).resize((W, H), Image.LANCZOS)
    thumb = Image.new("RGBA", (W, H))
    thumb.paste(bg, (0, 0))

    # 2. Dark gradient overlay (bottom 60%) for text readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for i in range(H):
        alpha = int(160 * max(0, (i - H * 0.35) / (H * 0.65)))
        draw_ov.line([(0, i), (W, i)], fill=(0, 0, 0, alpha))
    thumb = Image.alpha_composite(thumb, overlay)

    draw = ImageDraw.Draw(thumb)

    # 3. Episode badge (top-left)
    badge_text = f"EP {episode_num:02d}"
    badge_font = get_font(EPISODE_FONT_SIZE, bold=True)
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw = badge_bbox[2] - badge_bbox[0] + 40
    bh = badge_bbox[3] - badge_bbox[1] + 20
    badge_x, badge_y = 50, 40
    # Rounded badge background
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + bw, badge_y + bh],
        radius=12,
        fill=BADGE_COLOUR,
    )
    draw.text(
        (badge_x + 20, badge_y + 10),
        badge_text,
        font=badge_font,
        fill=(255, 255, 255, 255),
    )

    # 4. Title text (bottom-left area)
    title_font   = get_font(TITLE_FONT_SIZE, bold=True)
    channel_font = get_font(CHANNEL_FONT_SIZE)

    max_text_width = W - PHOTO_SIZE - PHOTO_MARGIN * 3 - 60
    lines = wrap_text(title, title_font, max_text_width)

    # Calculate total text block height
    line_height = TITLE_FONT_SIZE + 12
    total_text_h = len(lines) * line_height
    text_y = H - total_text_h - 80  # 80px from bottom

    for line in lines:
        draw_text_with_shadow(draw, (60, text_y), line, title_font)
        text_y += line_height

    # Channel name below title
    draw_text_with_shadow(
        draw,
        (60, text_y + 8),
        "I Have a Cause",
        channel_font,
        fill=(220, 180, 255, 220),
    )

    # 5. Person photo (bottom-right, circular)
    photo = load_image(photo_url)
    photo_circle = circle_crop(photo, PHOTO_SIZE)

    # White ring border around photo
    ring_size = PHOTO_SIZE + 8
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring)
    ring_draw.ellipse((0, 0, ring_size, ring_size), fill=(255, 255, 255, 200))
    photo_x = W - ring_size - PHOTO_MARGIN
    photo_y = H - ring_size - PHOTO_MARGIN
    thumb.paste(ring, (photo_x, photo_y), ring)
    thumb.paste(photo_circle, (photo_x + 4, photo_y + 4), photo_circle)

    # 6. Logo symbol (top-right)
    logo = svg_to_image(logo_svg, LOGO_SIZE)
    logo_x = W - LOGO_SIZE - 30
    logo_y = 20
    thumb.paste(logo, (logo_x, logo_y), logo)

    # 7. Thin pink line above title area
    line_y = H - total_text_h - 90
    draw.line([(60, line_y), (W - PHOTO_SIZE - PHOTO_MARGIN * 2, line_y)],
              fill=(220, 50, 120, 180), width=2)

    # 8. Save as JPEG (YouTube requires JPEG or PNG, max 2MB)
    final = thumb.convert("RGB")
    final.save(output, "JPEG", quality=92, optimize=True)
    print(f"✅ Thumbnail saved: {output}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bg_url",      required=True)
    parser.add_argument("--photo_url",   required=True)
    parser.add_argument("--logo_svg",    required=True)
    parser.add_argument("--episode_num", required=True, type=int)
    parser.add_argument("--title",       required=True)
    parser.add_argument("--language",    required=True, choices=["tamil", "english"])
    parser.add_argument("--output",      default="thumbnail.jpg")
    args = parser.parse_args()

    generate_thumbnail(
        bg_url      = args.bg_url,
        photo_url   = args.photo_url,
        logo_svg    = args.logo_svg,
        episode_num = args.episode_num,
        title       = args.title,
        language    = args.language,
        output      = args.output,
    )
