#!/usr/bin/env python3
"""Generate the 1200x630 OpenGraph image into public/og-image.png.

Run when the branding or headline changes:
  python3 ui/scripts/make-og-image.py
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
UI = HERE.parent
LOGO = UI / "src" / "assets" / "desearch-logo.png"
OUT = UI / "public" / "og-image.png"

W, H = 1200, 630
BG = (10, 14, 19)
TEXT = (244, 247, 251)
MUTED = (138, 150, 167)
BRAND = (59, 130, 246)

FONTS = "/System/Library/Fonts/Supplemental"
title_font = ImageFont.truetype(f"{FONTS}/Arial Bold.ttf", 72)
sub_font = ImageFont.truetype(f"{FONTS}/Arial.ttf", 34)
tag_font = ImageFont.truetype(f"{FONTS}/Arial Bold.ttf", 26)

img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)

# subtle brand glow band near the top
glow = Image.new("RGB", (W, H), BG)
gdraw = ImageDraw.Draw(glow)
gdraw.ellipse([W * 0.2, -360, W * 0.8, 200], fill=(18, 32, 58))
img = Image.blend(img, glow, 0.6)
draw = ImageDraw.Draw(img)

PAD = 90

# logo, scaled to a fixed height
logo = Image.open(LOGO).convert("RGBA")
lh = 56
lw = round(logo.width * lh / logo.height)
logo = logo.resize((lw, lh), Image.LANCZOS)
img.paste(logo, (PAD, 90), logo)

# headline
draw.text((PAD, 210), "AI Search Benchmark", font=title_font, fill=TEXT)
draw.text((PAD, 304), "Behavioral grading for AI-search providers,", font=sub_font, fill=MUTED)
draw.text((PAD, 348), "judged on what they actually return.", font=sub_font, fill=MUTED)

# provider row
providers = "Desearch · GPT-5-mini · Perplexity · Tavily · Exa"
draw.text((PAD, 470), providers, font=tag_font, fill=TEXT)

# three-metric chips
chips = ["Source relevance", "Answer quality", "Groundedness"]
x = PAD
for c in chips:
    bbox = draw.textbbox((0, 0), c, font=sub_font)
    cw = bbox[2] - bbox[0]
    draw.rounded_rectangle([x, 520, x + cw + 44, 575], radius=27,
                           outline=BRAND, width=2)
    draw.text((x + 22, 530), c, font=sub_font, fill=TEXT)
    x += cw + 44 + 20

img.save(OUT)
print(f"Wrote {OUT} ({W}x{H})")
