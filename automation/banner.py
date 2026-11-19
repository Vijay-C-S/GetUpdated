"""Generate a branded 1200x800 WebP banner for a post (no external images needed)."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 800
FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
FONT_CANDIDATES_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(candidates: list[str], size: int):
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _gradient() -> Image.Image:
    top, bottom = (79, 70, 229), (124, 58, 237)  # indigo -> violet (site theme #6366f1)
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = (x / W) * 0.45 + (y / H) * 0.55
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img


def make_banner(out: Path, company: str, role: str, tag: str = "Freshers Jobs",
                extra: str = "", site: str = "getupdated.in") -> Path:
    img = _gradient()
    d = ImageDraw.Draw(img)
    # decorative circles
    d.ellipse((W - 380, -180, W + 160, 360), outline=(165, 160, 255), width=3)
    d.ellipse((-160, H - 260, 260, H + 160), outline=(165, 160, 255), width=3)

    pad = 80
    f_tag, f_company, f_role, f_extra, f_site = (
        _font(FONT_CANDIDATES_BOLD, 30), _font(FONT_CANDIDATES_BOLD, 92),
        _font(FONT_CANDIDATES_BOLD, 50), _font(FONT_CANDIDATES_REG, 34),
        _font(FONT_CANDIDATES_BOLD, 32))

    # tag pill
    tag_text = tag.upper()
    tw = d.textlength(tag_text, font=f_tag)
    d.rounded_rectangle((pad, 90, pad + tw + 48, 146), radius=28, fill=(250, 204, 21))
    d.text((pad + 24, 101), tag_text, font=f_tag, fill=(30, 27, 75))

    y = 230
    company_lines = textwrap.wrap(company, width=18)[:2]
    for line in company_lines:
        d.text((pad, y), line, font=f_company, fill="white")
        y += 104
    d.text((pad, y + 6), "is hiring", font=f_extra, fill=(224, 222, 255))
    y += 70
    for line in textwrap.wrap(role, width=36)[:2]:
        d.text((pad, y), line, font=f_role, fill="white")
        y += 62
    if extra:
        y += 14
        for line in textwrap.wrap(extra, width=55)[:2]:
            d.text((pad, y), line, font=f_extra, fill=(224, 222, 255))
            y += 44

    # footer bar
    d.rectangle((0, H - 90, W, H), fill=(30, 27, 75))
    d.text((pad, H - 68), site, font=f_site, fill="white")
    cta = "Full details + Apply link inside"
    d.text((W - pad - d.textlength(cta, font=f_extra), H - 66), cta, font=f_extra, fill=(250, 204, 21))

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "WEBP", quality=82, method=6)
    return out


if __name__ == "__main__":
    # quick manual test: python banner.py out.webp "Company" "Role" "Tag" "extra"
    make_banner(Path(sys.argv[1]), *sys.argv[2:6])
