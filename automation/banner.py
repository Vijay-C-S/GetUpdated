"""Post banners (1200x800 WebP).

Every post gets a designed banner. Colours and the icon change with the post type
(govt / bank / internship / apprenticeship / WFH / jobs) and vary per post, the company logo is shown
when one is found, and the key facts (stipend, posts, eligibility) and last date are on the banner.
"""
from __future__ import annotations

import math
import re
import sys
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 800
FONT_DIR = Path(__file__).resolve().parent / "fonts"
FALLBACK = {"Regular": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf"],
            "Bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf"]}


def _font(weight: str, size: int):
    """Poppins (the site font, bundled in fonts/), else a system font."""
    for path in [FONT_DIR / f"Poppins-{weight}.ttf"] + [Path(p) for p in
                                                        FALLBACK["Regular" if weight == "Regular" else "Bold"]]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


# (background from, background to, accent) per post type; a post picks one by its name hash
THEMES = {
    "govt": [((15, 23, 42), (30, 64, 175), (245, 158, 11)), ((30, 27, 75), (29, 78, 216), (251, 191, 36)),
             ((12, 74, 110), (30, 58, 138), (253, 186, 116))],
    "bank": [((6, 78, 59), (13, 148, 136), (250, 204, 21)), ((20, 83, 45), (21, 128, 61), (253, 224, 71))],
    "intern": [((14, 116, 144), (37, 99, 235), (250, 204, 21)), ((15, 118, 110), (8, 145, 178), (254, 240, 138))],
    "apprentice": [((154, 52, 18), (219, 39, 119), (254, 215, 170)), ((124, 45, 18), (234, 88, 12), (254, 240, 138))],
    "wfh": [((6, 95, 70), (14, 116, 144), (167, 243, 208)), ((49, 46, 129), (13, 148, 136), (153, 246, 228))],
    "jobs": [((67, 56, 202), (124, 58, 237), (250, 204, 21)), ((30, 41, 59), (79, 70, 229), (56, 189, 248)),
             ((131, 24, 67), (76, 29, 149), (251, 207, 232)), ((17, 24, 39), (190, 24, 93), (253, 186, 116))],
}
ICON = {"govt": "building", "bank": "building", "intern": "cap", "apprentice": "gear", "wfh": "laptop",
        "jobs": "briefcase"}


def _kind(tag: str) -> str:
    t = (tag or "").lower()
    for key, kind in (("govt", "govt"), ("government", "govt"), ("bank", "bank"), ("intern", "intern"),
                      ("apprentic", "apprentice"), ("home", "wfh"), ("remote", "wfh")):
        if key in t:
            return kind
    return "jobs"


def _gradient(c1, c2) -> Image.Image:
    mask = Image.linear_gradient("L").rotate(-50, expand=True)
    m = mask.width // 2
    mask = mask.crop((m - 90, m - 90, m + 90, m + 90)).resize((W, H), Image.BICUBIC)
    return Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mask)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _fit(d, text: str, weight: str, sizes: range, max_w: int, max_lines: int):
    """Largest font size where the text fits in max_lines; the last line gets '...' if it never fits."""
    for size in sizes:
        font = _font(weight, size)
        lines = _wrap(d, text, font, max_w)
        if len(lines) <= max_lines and all(d.textlength(l, font=font) <= max_w for l in lines):
            return font, lines
    lines = lines[:max_lines]
    while lines and d.textlength(lines[-1] + "...", font=font) > max_w and " " in lines[-1]:
        lines[-1] = lines[-1].rsplit(" ", 1)[0]
    lines[-1] += "..."
    return font, lines


def _icon(d: ImageDraw.ImageDraw, kind: str, cx: int, cy: int, s: int, bg) -> None:
    """Big simple white icon (drawn, no image files)."""
    w, soft = (255, 255, 255, 235), (255, 255, 255, 170)
    if kind == "cap":
        d.rounded_rectangle((cx - .55 * s, cy - .1 * s, cx + .55 * s, cy + .5 * s), radius=int(.18 * s), fill=soft)
        d.polygon([(cx - s, cy - .25 * s), (cx, cy - .65 * s), (cx + s, cy - .25 * s), (cx, cy + .15 * s)], fill=w)
        d.line([(cx + .78 * s, cy - .2 * s), (cx + .78 * s, cy + .5 * s)], fill=w, width=max(4, s // 18))
        d.ellipse((cx + .7 * s, cy + .45 * s, cx + .86 * s, cy + .61 * s), fill=w)
    elif kind == "building":
        d.polygon([(cx - s, cy - .38 * s), (cx, cy - .85 * s), (cx + s, cy - .38 * s)], fill=w)
        d.rectangle((cx - s, cy - .34 * s, cx + s, cy - .22 * s), fill=w)
        for i in range(4):
            x = cx - .78 * s + i * .52 * s
            d.rectangle((x - .09 * s, cy - .14 * s, x + .09 * s, cy + .42 * s), fill=soft)
        d.rectangle((cx - 1.08 * s, cy + .48 * s, cx + 1.08 * s, cy + .62 * s), fill=w)
    elif kind == "laptop":
        d.rounded_rectangle((cx - .8 * s, cy - .62 * s, cx + .8 * s, cy + .34 * s), radius=int(.08 * s),
                            outline=w, width=max(6, s // 10))
        d.polygon([(cx - s, cy + .42 * s), (cx + s, cy + .42 * s), (cx + .88 * s, cy + .58 * s),
                   (cx - .88 * s, cy + .58 * s)], fill=w)
    elif kind == "gear":
        r_out, r_in = .8 * s, .62 * s
        for k in range(8):
            a = k * math.pi / 4
            pts = []
            for da, r in ((-.17, r_in), (-.12, r_out), (.12, r_out), (.17, r_in)):
                pts.append((cx + r * math.cos(a + da), cy + r * math.sin(a + da)))
            d.polygon(pts, fill=w)
        d.ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in), fill=w)
        d.ellipse((cx - .28 * s, cy - .28 * s, cx + .28 * s, cy + .28 * s), fill=(0, 0, 0, 0))
    else:  # briefcase
        t = max(8, s // 9)
        d.rounded_rectangle((cx - .36 * s, cy - .78 * s, cx + .36 * s, cy - .25 * s), radius=int(.14 * s),
                            outline=w, width=t)
        d.rounded_rectangle((cx - s, cy - .45 * s, cx + s, cy + .62 * s), radius=int(.14 * s), fill=w)
        d.rectangle((cx - s, cy - .02 * s, cx + s, cy + .06 * s), fill=(*bg, 255))
        d.rounded_rectangle((cx - .14 * s, cy - .1 * s, cx + .14 * s, cy + .14 * s), radius=6, fill=soft)


def _trim(logo: Image.Image) -> Image.Image:
    """Crop away transparent / near-white padding so the visible logo is centred and fills the card."""
    flat = Image.new("RGBA", logo.size, "white")
    flat.alpha_composite(logo)
    ink = flat.convert("L").point(lambda v: 255 if v < 245 else 0)
    box = ink.getbbox()
    if not box or (box[2] - box[0]) < 8 or (box[3] - box[1]) < 8:
        return logo
    return logo.crop(box)


def _logo_badge(logo: Image.Image, max_w: int = 380, max_h: int = 270) -> Image.Image:
    """Logo on a white rounded card with a soft shadow (RGBA)."""
    logo = _trim(logo.convert("RGBA"))
    scale = min(max_w / logo.width, max_h / logo.height, 4.0)
    logo = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
    cw, ch = max(logo.width + 60, 280), max(logo.height + 60, 280)
    sh = 30
    out = Image.new("RGBA", (cw + 2 * sh, ch + 2 * sh), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((sh, sh + 10, sh + cw, sh + ch + 10), radius=36, fill=(0, 0, 0, 110))
    out = Image.alpha_composite(out, shadow.filter(ImageFilter.GaussianBlur(16)))
    ImageDraw.Draw(out).rounded_rectangle((sh, sh, sh + cw, sh + ch), radius=36, fill="white")
    out.alpha_composite(logo, (sh + (cw - logo.width) // 2, sh + (ch - logo.height) // 2))
    return out


def make_banner(out: Path, company: str, role: str, tag: str = "Freshers Jobs", extra: str = "",
                site: str = "getupdated.in", chips: list[str] | None = None, deadline: str = "",
                logo: Image.Image | None = None) -> Path:
    kind = _kind(tag)
    seed = zlib.crc32(f"{company}|{role}".encode())
    c1, c2, accent = THEMES[kind][seed % len(THEMES[kind])]
    img = _gradient(c1, c2).convert("RGBA")

    # soft glows + dot grid for depth
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    gx = 860 + (seed >> 4) % 200
    g.ellipse((gx - 330, -260, gx + 330, 400), fill=(*accent, 85))
    g.ellipse((-260, H - 330, 300 + (seed >> 8) % 160, H + 230), fill=(255, 255, 255, 40))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(110)))
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    for y in range(40, H - 140, 30):
        for x in range(W - 420, W - 20, 30):
            dd.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 255, 255, 34))
    cx, cy = 958, 318
    dd.ellipse((cx - 190, cy - 190, cx + 190, cy + 190), fill=(255, 255, 255, 26))
    dd.ellipse((cx - 232, cy - 232, cx + 232, cy + 232), outline=(255, 255, 255, 60), width=3)
    if logo is None:
        _icon(dd, ICON[kind], cx, cy + 10, 128, c2)
    img = Image.alpha_composite(img, deco)
    if logo is not None:
        badge = _logo_badge(logo)
        img.alpha_composite(badge, (cx - badge.width // 2, cy - badge.height // 2))

    d = ImageDraw.Draw(img)
    pad, col_w = 80, 620
    # tag pill
    f_tag = _font("Bold", 26)
    tag_text = (tag or "Jobs").upper()
    tw = d.textlength(tag_text, font=f_tag)
    d.rounded_rectangle((pad, 72, pad + tw + 52, 126), radius=27, fill=accent)
    d.text((pad + 26, 99), tag_text, font=f_tag, fill=(17, 24, 39), anchor="lm")

    # company / is hiring / role / extra
    y = 160
    f_co, co_lines = _fit(d, company, "ExtraBold", range(96, 71, -4), col_w, 1)  # one line if it stays big
    if co_lines[-1].endswith("..."):
        f_co, co_lines = _fit(d, company, "ExtraBold", range(88, 55, -4), col_w, 2)
    for line in co_lines:
        d.text((pad, y), line, font=f_co, fill="white")
        y += int(f_co.size * 1.1)
    f_hire = _font("SemiBold", 32)
    y += 12
    d.text((pad, y), "is hiring", font=f_hire, fill=accent)
    y += 62
    f_role, role_lines = _fit(d, role, "Bold", range(52, 31, -4), col_w + 20, 3 if len(co_lines) == 1 else 2)
    for line in role_lines:
        d.text((pad, y), line, font=f_role, fill="white")
        y += int(f_role.size * 1.22)
    if extra and y < H - 290:
        f_ex, ex_lines = _fit(d, extra, "Regular", range(30, 23, -2), col_w + 20, 1)
        d.text((pad, y + 8), ex_lines[0], font=f_ex, fill=(226, 232, 240))

    # key fact chips
    f_chip = _font("SemiBold", 28)
    x, chip_y = pad, H - 200
    for chip in [c.strip() for c in (chips or []) if c and c.strip()][:3]:
        cw = d.textlength(chip, font=f_chip) + 52
        if x + cw > W - pad:
            break
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((x, chip_y, x + cw, chip_y + 62), radius=31,
                                                fill=(255, 255, 255, 46), outline=(255, 255, 255, 110), width=2)
        img = Image.alpha_composite(img, layer)
        d = ImageDraw.Draw(img)
        d.text((x + 26, chip_y + 31), chip, font=f_chip, fill="white", anchor="lm")
        x += cw + 16

    # footer: site + last date badge (or call to action)
    foot = tuple(int(c * .45) for c in c1)
    d.rectangle((0, H - 100, W, H), fill=foot)
    d.ellipse((pad, H - 58, pad + 14, H - 44), fill=accent)
    d.text((pad + 28, H - 50), site, font=_font("Bold", 32), fill="white", anchor="lm")
    if deadline:
        f_dl, f_dv = _font("SemiBold", 22), _font("Bold", 30)
        label, val = "LAST DATE", deadline
        bw = d.textlength(label, font=f_dl) + d.textlength(val, font=f_dv) + 70
        d.rounded_rectangle((W - pad - bw, H - 80, W - pad, H - 20), radius=30, fill=(239, 68, 68))
        d.text((W - pad - bw + 26, H - 50), label, font=f_dl, fill=(254, 226, 226), anchor="lm")
        d.text((W - pad - 26, H - 50), val, font=f_dv, fill="white", anchor="rm")
    else:
        d.text((W - pad, H - 50), "Full details + apply link inside", font=_font("SemiBold", 28),
               fill=accent, anchor="rm")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "WEBP", quality=85, method=6)
    return out


# --------------------------------------------------------------------------- company image
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def _get_image(url: str):
    import io

    import requests
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    if "svg" in r.headers.get("content-type", "") or url.lower().split("?")[0].endswith(".svg"):
        return None  # Pillow cannot read SVG
    img = Image.open(io.BytesIO(r.content))
    img.load()
    return img


def _page_logos(page_url: str) -> list[str]:
    """Logo candidates on a page: the FIRST <img> logo (the site's own, in the header; later ones are
    often partner / govt portal logos) and the apple-touch-icon."""
    from urllib.parse import urljoin

    import requests
    from bs4 import BeautifulSoup
    r = requests.get(page_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    base, logos = r.url, []
    for img in soup.find_all("img", src=True):
        hay = " ".join([img.get("src", ""), img.get("alt", ""), " ".join(img.get("class", [])), img.get("id", "")]).lower()
        if "logo" in hay:
            logos.append(urljoin(base, img["src"]))
            break
    logos += [urljoin(base, l["href"]) for l in soup.find_all("link", href=True)
              if "apple-touch-icon" in " ".join(l.get("rel", [])).lower()]
    return logos


def _usable(img) -> bool:
    return bool(img) and (min(img.width, img.height) >= 90 or (img.width >= 160 and img.height >= 40))


WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = {"User-Agent": "JobBlogLogoFetcher/1.0 (low-volume; one logo per article)"}
LOGO_WORD = re.compile(r"logo|emblem|seal|insignia|crest", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _wiki(params: dict) -> dict:
    import time

    import requests
    for wait in (4, 12, 30, 0):  # Wikipedia rate-limits bursts (429): wait and retry
        r = requests.get(WIKI_API, params={**params, "format": "json", "formatversion": 2}, headers=WIKI_UA, timeout=20)
        if r.status_code != 429 or not wait:
            break
        time.sleep(wait)
    r.raise_for_status()
    return r.json()


def wiki_logo(names: list[str]) -> Image.Image | None:
    """Logo from the organisation's English Wikipedia infobox (PNG render, works for SVG logos).
    Names are tried in order (full name, short name, parent organisation); a name must be a page or a
    redirect to one (e.g. 'IOCL' -> Indian Oil Corporation), never a fuzzy search hit. Only the first
    infobox image is used, and only if its file is clearly a logo (name says logo/emblem/seal or is the
    organisation's own name), so photos of buildings / rockets are never used."""
    for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
        try:
            pages = _wiki({"action": "query", "titles": name, "redirects": 1}).get("query", {}).get("pages", [])
            if not pages or pages[0].get("missing") or pages[0].get("invalid"):
                continue
            title = pages[0]["title"]
            text = _wiki({"action": "parse", "page": title, "prop": "text", "section": 0}).get("parse", {}).get("text", "")
        except Exception:  # noqa: BLE001
            continue
        from bs4 import BeautifulSoup
        box = BeautifulSoup(text, "html.parser").select_one("table.infobox")
        img = box and next((i for i in box.find_all("img") if int(i.get("width") or 0) >= 60), None)
        if not img:
            continue
        src = "https:" + img["src"] if img["src"].startswith("//") else img["src"]
        file = re.sub(r"^\d+px-", "", src.split("?")[0].rsplit("/", 1)[-1])
        stem = _norm(re.sub(r"\.(svg|png|jpe?g|webp|gif)$", "", re.sub(r"\.(svg|png|jpe?g|webp|gif)$", "", file, flags=re.I), flags=re.I))
        if not (LOGO_WORD.search(file) or stem in {_norm(title), _norm(name)}):
            continue
        for url in (re.sub(r"/\d+px-", "/600px-", src), src):  # bigger render first; original if too small
            try:
                logo = _get_image(url)
                if _usable(logo):
                    return logo
            except Exception:  # noqa: BLE001
                pass
    return None


def company_logo(page_urls: list[str], names: list[str] | None = None) -> Image.Image | None:
    """The company's logo for the post image, best source first: its Wikipedia infobox logo (clean,
    high resolution), its website / official page header logo, Google's site icon (by domain).
    None if nothing good enough is found."""
    from urllib.parse import urlparse
    logo = wiki_logo(names or [])
    if logo is not None:
        return logo
    pages = [u for u in page_urls if u and u.startswith("http")]
    for page in [u for u in pages if not u.lower().split("?")[0].endswith(".pdf")]:
        try:
            candidates = _page_logos(page)
        except Exception:  # noqa: BLE001
            continue
        for url in candidates[:4]:
            try:
                img = _get_image(url)
                if _usable(img):
                    return img
            except Exception:  # noqa: BLE001
                pass
    for dom in dict.fromkeys(urlparse(u).netloc for u in pages):
        try:
            img = _get_image(f"https://www.google.com/s2/favicons?domain={dom}&sz=256")
            if img and min(img.width, img.height) >= 120:
                return img
        except Exception:  # noqa: BLE001
            pass
    return None


if __name__ == "__main__":
    # quick manual test: python banner.py out.webp "Company" "Role" "Tag" "extra" "chip1|chip2" "12 Dec 2026"
    a = sys.argv[1:] + [""] * 7
    make_banner(Path(a[0]), a[1], a[2], a[3] or "Freshers Jobs", a[4], chips=[c for c in a[5].split("|") if c],
                deadline=a[6])
