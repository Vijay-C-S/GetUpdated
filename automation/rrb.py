"""RRB (Railway Recruitment Boards) notices from the official apply portal.

www.rrbapply.gov.in is a JavaScript app: a plain fetch gets an empty shell, and the regional
rrb*.gov.in sites refuse/time out. The portal's own home page loads its notice list from a public
JSON endpoint (no login), and each notice links the official CEN PDF on rrbapply.gov.in.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

PORTAL = "https://www.rrbapply.gov.in"
WHATS_NEW = "https://api.recruitmentrrb.in/api/Advertisement/WhatsNew"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
           "Origin": PORTAL, "Referer": PORTAL + "/", "Accept": "application/json"}
CEN_NO = re.compile(r"CEN\)?\s*(?:No\.?)?\s*[-:]?\s*(\d{1,2})\s*/\s*(\d{4})", re.I)


def is_rrb_url(url: str) -> bool:
    dom = urlparse(url or "").netloc.lower().removeprefix("www.")
    return dom == "rrbapply.gov.in" or dom == "recruitmentrrb.in" or bool(re.fullmatch(r"rrb[a-z]*\.gov\.in", dom))


def cen_key(text: str) -> str:
    """'08/2026' from any text that names a CEN number, else ''."""
    m = CEN_NO.search(text or "")
    return f"{int(m.group(1)):02d}/{m.group(2)}" if m else ""


def notices(include_hindi: bool = False) -> list[dict]:
    """Current RRB notices: [{title, url (official PDF), summary, cen, start, end}]."""
    r = requests.get(WHATS_NEW, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise ValueError(f"RRB WhatsNew API: {data.get('error')}")
    out = []
    for n in data.get("result") or []:
        title = re.sub(r"\s+", " ", n.get("description") or "").strip()
        doc = (n.get("documentUrl") or n.get("link") or "").strip()
        if not title or not doc:
            continue
        if not include_hindi and re.search(r"\bhindi\b", title, re.I):
            continue  # same notice as the English one
        url = doc if doc.startswith("http") else PORTAL + "/" + doc.lstrip("/")
        start, end = (n.get("startDate") or "")[:10], (n.get("endDate") or "")[:16].replace("T", " ")
        out.append({"title": title, "url": url, "cen": cen_key(title), "start": start, "end": end,
                    "summary": f"Official RRB notice on {PORTAL}. Application window: {start} to {end} "
                               f"(as listed on the RRB apply portal)."})
    return out


def match(text: str, items: list[dict]) -> list[dict]:
    """Notices that a lead refers to: same CEN number, else every open notice (let the writer pick)."""
    key = cen_key(text)
    hits = [n for n in items if key and n["cen"] == key]
    return hits or items
