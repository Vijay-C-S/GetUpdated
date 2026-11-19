"""Watchdog: scan job sources for new postings and record them as candidates.

No AI is used here. Two source types (see sources.json):
  rss  - RSS/Atom feed; every new item is a candidate.
  page - an HTML page (careers / notifications list); every NEW link whose text
         matches the keywords is a candidate. The first scan of a page only
         records a baseline, so you are not flooded with old postings.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from common import (AUTO_DIR, CONFIG, SOURCES_STATE_FILE, FileLock, existing_blog_slugs,
                    load_jobs, log, now_iso, read_json, save_jobs, similarity, url_id,
                    write_json)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) GetUpdatedWatchdog/1.0"}


def fetch(url: str) -> str:
    for attempt in range(3):
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 429 and attempt < 2:  # rate limited: back off and retry
            time.sleep(30 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.text
    resp.raise_for_status()
    return resp.text


def parse_feed(xml_text: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text.encode("utf-8", errors="replace"))
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = summary = ""
        for child in item:
            ctag = child.tag.split("}")[-1]
            if ctag == "title":
                title = (child.text or "").strip()
            elif ctag == "link":
                link = (child.text or child.attrib.get("href") or "").strip()
            elif ctag in ("description", "summary"):
                summary = BeautifulSoup(child.text or "", "html.parser").get_text(" ", strip=True)[:400]
        if title and link:
            items.append({"title": title, "url": link, "summary": summary})
    return items


def parse_page(html: str, base_url: str, link_pattern: str | None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = urljoin(base_url, a["href"].strip())
        if len(text) < 8 or not href.startswith("http") or href in seen:
            continue
        if link_pattern and not re.search(link_pattern, href + " " + text, re.I):
            continue
        seen.add(href)
        found.append({"title": text[:200], "url": href, "summary": ""})
    return found


ENTRY_WORDS = ("intern", "graduate", "fresher", "trainee", "new grad", "apprentice", "entry",
               "associate software", "sde 1", "sde i", "sde-1", "engineer i", "engineer 1",
               "software engineer - i", "junior", "campus", "2025", "2026", "2027")
SENIOR_WORDS = ("senior", "sr.", "sr ", "lead", "staff", "principal", "manager", "director", "head",
                " ii", " iii", " iv", "architect", "vp", "specialist ii", "engineer 2", "engineer 3")
LOCATION_WORDS = ("bengaluru", "bangalore", "remote", "work from home", "anywhere in india")


def _strip_html(text: str, limit: int = 5000) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)[:limit]


def parse_ats(provider: str, company: str, name: str) -> list[dict]:
    """Official company job boards. Every URL here IS the official posting."""
    out = []
    if provider == "greenhouse":
        data = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true",
                            headers=HEADERS, timeout=30).json()
        for j in data.get("jobs", []):
            out.append({"title": j["title"], "url": j["absolute_url"],
                        "location": (j.get("location") or {}).get("name", ""),
                        "text": _strip_html(html_unescape(j.get("content", "")))})
    elif provider == "lever":
        data = requests.get(f"https://api.lever.co/v0/postings/{company}?mode=json",
                            headers=HEADERS, timeout=30).json()
        for j in data:
            cats = j.get("categories") or {}
            locs = " / ".join([cats.get("location") or ""] + list(cats.get("allLocations") or []))
            text = (j.get("descriptionPlain") or "") + " " + " ".join(
                f"{l.get('text')}: {_strip_html(l.get('content'), 1500)}" for l in j.get("lists", []))
            out.append({"title": j["text"], "url": j["hostedUrl"], "location": locs,
                        "text": text[:5000], "workplace": j.get("workplaceType", "")})
    elif provider == "ashby":
        data = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{company}",
                            headers=HEADERS, timeout=30).json()
        for j in data.get("jobs", []):
            locs = j.get("location", "") + " " + " ".join(
                str(s.get("location", "")) for s in j.get("secondaryLocations") or [])
            out.append({"title": j["title"], "url": j["jobUrl"], "location": locs,
                        "text": (j.get("descriptionPlain") or "")[:5000],
                        "workplace": "remote" if j.get("isRemote") else ""})
    items = []
    for j in out:
        t, loc = j["title"].lower(), (j["location"] + " " + j.get("workplace", "")).lower()
        if not any(w in loc for w in LOCATION_WORDS):
            continue
        if "remote" in loc and "india" not in loc and not any(w in loc for w in LOCATION_WORDS[:2]):
            continue  # remote but not open to India
        if any(w in f" {t} " for w in SENIOR_WORDS) or not any(w in t for w in ENTRY_WORDS):
            continue
        items.append({"title": f"{name} hiring {j['title']} ({j['location'][:60]})", "url": j["url"],
                      "summary": f"Official {provider} posting. Location: {j['location'][:80]}",
                      "full_text": j["text"]})
    return items


def html_unescape(s: str) -> str:
    import html as _html
    return _html.unescape(s or "")


def keyword_ok(text: str) -> bool:
    t = text.lower()
    if any(k in t for k in CONFIG["keywords_exclude"]):
        return False
    return any(k in t for k in CONFIG["keywords_include"])


def is_duplicate(title: str, jobs: dict, slugs: list[str]) -> str | None:
    for slug in slugs:
        if similarity(title, slug.replace("-", " ")) >= 0.6:
            return f"similar to existing blog {slug}"
    for job in jobs.values():
        if job.get("status") in ("rejected",):
            continue
        if similarity(title, job.get("title", "")) >= 0.7:
            return f"similar to job {job['id']}"
    return None


def scan() -> int:
    sources = read_json(AUTO_DIR / "sources.json", [])
    src_state = read_json(SOURCES_STATE_FILE, {})
    slugs = existing_blog_slugs()
    new_count = 0

    with FileLock("jobs", max_age=120, wait=60):
        jobs = load_jobs()
        for src in sources:
            if not src.get("enabled", True):
                continue
            name, kind = src["name"], src.get("type", "rss")
            time.sleep(3)  # be polite; avoids 429s
            try:
                if kind == "ats":
                    items = []
                    for comp in src.get("companies", []):
                        slug, cname = (comp, comp.title()) if isinstance(comp, str) else (comp["slug"], comp["name"])
                        try:
                            items += parse_ats(src["provider"], slug, cname)
                        except Exception as exc:  # noqa: BLE001
                            log(f"watchdog: {src['provider']}/{slug} failed: {exc}")
                else:
                    text = fetch(src["url"])
                    items = parse_feed(text) if kind == "rss" else parse_page(text, src["url"], src.get("link_pattern"))
            except Exception as exc:  # noqa: BLE001
                log(f"watchdog: source '{name}' failed: {exc}")
                continue

            state = src_state.setdefault(name, {"seen": [], "initialized": False})
            seen = set(state["seen"])
            baseline = kind == "page" and not state["initialized"]
            added = 0
            for it in items:
                jid = url_id(it["url"])
                if jid in seen or jid in jobs:
                    continue
                seen.add(jid)
                if baseline:
                    continue
                if kind != "ats" and not keyword_ok(it["title"] + " " + it["summary"]):
                    continue
                dup = is_duplicate(it["title"], jobs, slugs)
                jobs[jid] = {
                    "id": jid, "title": it["title"], "url": it["url"], "summary": it["summary"],
                    "source": name, "source_hint": src.get("hint", ""), "source_type": kind,
                    "full_text": it.get("full_text", ""),
                    "status": "rejected" if dup else "candidate",
                    "reason": dup or "", "found_at": now_iso(), "updated_at": now_iso(),
                }
                if not dup:
                    added += 1
            state["seen"] = list(seen)[-3000:]
            state["initialized"] = True
            state["last_scan"] = now_iso()
            if baseline:
                log(f"watchdog: '{name}' baseline recorded ({len(items)} links)")
            elif added:
                log(f"watchdog: '{name}' -> {added} new candidate(s)")
            new_count += added
        save_jobs(jobs)
    write_json(SOURCES_STATE_FILE, src_state)
    log(f"watchdog: done, {new_count} new candidate(s)")
    return new_count


if __name__ == "__main__":
    scan()
