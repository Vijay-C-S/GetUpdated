"""Writer: Sonnet verifies a queued job and writes the article; Python builds the page.

Token savers:
- Python pre-fetches the lead page and the official links (incl. PDF text via pypdf),
  so the model needs few or no web calls; leads with no official link are dropped before
  any model call.
- The model writes only body.html (article) + meta.json. The head, SEO tags, JSON-LD,
  navbar, TOC, Also Read, FAQ, sidebar and footer are rendered here from templates/.
- Static instructions live in the system prompt (cache-friendly across jobs).

Output: automation/state/drafts/<id>/{post.html, meta.json, banner.webp}
"""
from __future__ import annotations

import html
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from banner import company_image, make_banner
from claude_cli import run_claude
from common import (AUTO_DIR, CONFIG, DRAFTS_DIR, REPO_DIR, FileLock, existing_blog_slugs, get_job,
                    git_is_clean, log, now_iso, run, today_ist, update_job)

WRITER_TOOLS = ["WebFetch", "WebSearch", "Write"]
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
AGGREGATORS = ("freejobalert", "freshersnow", "offcampusjobs4u", "fresheropenings", "karnatakacareers",
               "sarkari", "adda247", "jagranjosh", "testbook", "news.google", "careerpower")
SKIP_DOMAINS = ("facebook.", "twitter.", "x.com", "instagram.", "t.me", "telegram.", "whatsapp.",
                "youtube.", "youtu.be", "linkedin.com/shareArticle", "pinterest.", "play.google",
                "apps.apple", "wa.me", "google.com/search", "reddit.com", "/share", "submit?url", "sharer", "mailto:")
OFFICIAL_HINTS = (".gov.in", ".nic.in", ".res.in", ".ac.in", ".edu", ".org.in", "careers", "career",
                  "jobs", "recruit", "apply", "forms.gle", "docs.google.com/forms", "lever.co",
                  "greenhouse.io", "myworkdayjobs", "ashbyhq", "smartrecruiters", "naukri.com/job-listings",
                  ".pdf", "notification")


# --------------------------------------------------------------------------- pre-fetch
def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def fetch_text(url: str, limit: int = 3500) -> str:
    """Readable text of an HTML page or PDF (first pages). Raises on failure."""
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(r.content))
        text = " ".join((p.extract_text() or "") for p in reader.pages[:6])
        return re.sub(r"\s+", " ", text)[:limit * 2]
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    return re.sub(r"\s+", " ", main.get_text(" ", strip=True))[:limit]


def official_links(page_html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    lead_dom, seen, scored = _domain(base), set(), []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].strip())
        dom = _domain(href)
        if not href.startswith("http") or href in seen or dom == lead_dom:
            continue
        if any(s in href.lower() for s in SKIP_DOMAINS) or any(g in dom for g in AGGREGATORS):
            continue
        seen.add(href)
        label = a.get_text(" ", strip=True)[:80]
        hay = (href + " " + label).lower()
        score = sum(h in hay for h in OFFICIAL_HINTS) + ("official" in hay) * 2 + ("notification" in hay)
        if score:
            scored.append((score, href, label))
    scored.sort(key=lambda x: -x[0])
    return [(h, l) for _, h, l in scored[:6]]


LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*?-)?(\d{6,})", re.I)


def linkedin_posting(url: str) -> tuple[str, str, str] | None:
    """(canonical view URL, posting text, external apply URL or "") for a LinkedIn job link.
    Tries the public logged-out /jobs/view page, then LinkedIn's guest view of the same posting.
    One posting per lead you approved, nothing is crawled."""
    m = LINKEDIN_JOB.search(url or "")
    if not m:
        return None
    jid = m.group(1)
    hdrs = {**HEADERS, "Accept-Language": "en-US,en;q=0.9"}
    soup, errors = None, []
    for u in (f"https://www.linkedin.com/jobs/view/{jid}",  # public page (logged-out view)
              f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"):  # rate-limited from the VM
        try:
            r = requests.get(u, headers=hdrs, timeout=30)
            r.raise_for_status()
            if "show-more-less-html__markup" in r.text or "description__text" in r.text:
                soup = BeautifulSoup(r.text, "html.parser")
                break
            errors.append(f"{u}: no posting text (login wall?)")
        except requests.RequestException as exc:
            errors.append(str(exc)[:120])
    if soup is None:
        raise ValueError("; ".join(errors))
    pick = lambda sel: (soup.select_one(sel).get_text(" ", strip=True) if soup.select_one(sel) else "")  # noqa: E731
    criteria = " | ".join(li.get_text(": ", strip=True) for li in soup.select(".description__job-criteria-item"))
    desc = pick(".show-more-less-html__markup") or pick(".description__text")
    apply = ""
    code = soup.find("code", id="applyUrl")
    if code:
        found = re.search(r"https?://[^\"'<>\s]+", code.decode_contents())
        apply = html.unescape(found.group(0)) if found else ""
    text = (f"Title: {pick('.top-card-layout__title')}\nCompany: {pick('.topcard__org-name-link')}\n"
            f"Location: {pick('.topcard__flavor--bullet')}\nPosted: {pick('.posted-time-ago__text')}\n"
            f"{criteria}\nApply: {'external link ' + apply if apply else 'LinkedIn Easy Apply'}\n\n{desc}")
    if len(desc) < 150:
        raise ValueError("LinkedIn posting text not available (closed or blocked)")
    return f"https://www.linkedin.com/jobs/view/{jid}", re.sub(r"[ \t]+", " ", text)[:6000], apply


def prefetch(job: dict) -> dict:
    """Return {'lead_text', 'links', 'excerpts'}; raise ValueError if no official source is reachable."""
    if job.get("source_type") == "ats":
        return {"lead_text": "", "links": [(job["url"], "official posting")],
                "excerpts": [(job["url"], job.get("full_text") or fetch_text(job["url"], 5000))]}
    if job.get("official"):  # official govt/bank/PSU page or PDF: the lead itself is the source
        return {"lead_text": "", "links": [(job["url"], "official notice")],
                "excerpts": [(job["url"], fetch_text(job["url"], 5000))]}
    if job.get("source", "").startswith("manual") or job.get("source_type") == "email":
        # You sent it (Telegram / forwarded WhatsApp post) or it came from a LinkedIn/Naukri alert.
        # Never skip: the writer searches for the official posting itself if needed.
        pre = {"lead_text": "", "links": [], "excerpts": []}
        li_url = job.get("url") or next((u for u in re.findall(r"https?://\S+", job.get("forwarded_text", ""))
                                         if LINKEDIN_JOB.search(u)), "")
        if LINKEDIN_JOB.search(li_url):
            try:
                view, text, apply = linkedin_posting(li_url)
                pre["links"] = [(view, "employer's LinkedIn posting")] + ([(apply, "employer's apply link")] if apply else [])
                pre["excerpts"] = [(view + " (EMPLOYER'S LINKEDIN POSTING)", text)]
                return pre
            except Exception as exc:  # noqa: BLE001
                log(f"writer: LinkedIn posting fetch failed for {li_url}: {exc}")
        if not job.get("url"):
            return pre
        try:
            r = requests.get(job["url"], headers=HEADERS, timeout=30)
            r.raise_for_status()
            pre["lead_text"] = fetch_text(job["url"], 4000)
            pre["links"] = official_links(r.text, job["url"])
            for href, _label in pre["links"][:3]:
                try:
                    txt = fetch_text(href, 3500)
                    if len(txt) > 200:
                        pre["excerpts"].append((href, txt))
                        break
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log(f"writer: prefetch {job['url']} failed: {exc}")
        return pre
    r = requests.get(job["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    lead_text = fetch_text(job["url"], 2500)
    links = official_links(r.text, job["url"])
    if not links:
        raise ValueError("lead page has no official link to verify against (skipped before writing)")
    excerpts = []
    for href, _label in links[:4]:
        try:
            txt = fetch_text(href, 3500)
            if len(txt) > 200:
                excerpts.append((href, txt))
        except Exception as exc:  # noqa: BLE001
            log(f"writer: prefetch {href} failed: {exc}")
        if len(excerpts) == 2:
            break
    return {"lead_text": lead_text, "links": links, "excerpts": excerpts}


# --------------------------------------------------------------------------- prompt
def build_user_prompt(job: dict, draft_dir: Path, pre: dict) -> str:
    d = today_ist()
    parts = [
        f"TODAY: {d.isoformat()} (IST)",
        f"DRAFT FOLDER (write body.html and meta.json here, nowhere else): {draft_dir.as_posix()}",
        "LEAD: " + json.dumps({k: job.get(k) for k in ("title", "url", "source", "summary", "company",
                                                        "role", "category", "manual_note") if job.get(k)},
                              ensure_ascii=False),
    ]
    if job.get("source_hint"):
        parts.append(f"HINT: {job['source_hint']}")
    if job.get("forwarded_text"):
        parts.append("POST THE OWNER FORWARDED (from a WhatsApp/Telegram channel, NOT a fact source; "
                     "use it to identify the job, then verify on the official site):\n" + job["forwarded_text"][:1500])
    if pre["lead_text"]:
        parts.append("LEAD PAGE TEXT (aggregator, NOT a fact source):\n" + pre["lead_text"])
    if pre["links"]:
        parts.append("CANDIDATE OFFICIAL LINKS:\n" + "\n".join(f"- {h} ({l})" for h, l in pre["links"]))
    for href, txt in pre["excerpts"]:
        parts.append(f"OFFICIAL PAGE EXCERPT from {href}:\n{txt}")
    if not pre["excerpts"]:
        parts.append("No official page could be pre-fetched: find and read it with WebFetch/WebSearch.")
    recent = sorted(existing_blog_slugs())
    parts.append("SLUGS ALREADY USED (pick a new one): " + ", ".join(recent[-120:]))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- page assembly
def latest_posts(n: int = 6) -> list[str]:
    out = run(["git", "log", "--diff-filter=A", "--name-only", "--format=", "-n", "60", "--", "blogs/"])
    seen = []
    for line in out.splitlines():
        line = line.strip()
        if line.endswith(".html") and line not in seen and (REPO_DIR / line).exists():
            seen.append(line)
        if len(seen) == n:
            break
    return seen


def post_title(path: Path) -> str:
    m = re.search(r'<h1 class="article-title">(.*?)</h1>', path.read_text(encoding="utf-8"), re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else path.stem.replace("-", " ").title()


def post_info(path: Path) -> dict:
    """Title + banner image of an existing post, for Recent Posts / Read Next blocks."""
    text = path.read_text(encoding="utf-8")
    img = ""
    for tag in re.findall(r"<img\b[^>]*>", text):
        if 'class="featured-image"' in tag:
            m = re.search(r'src="([^"]+)"', tag)
            img = m.group(1) if m else ""
            break
    if not img:
        m = re.search(r'<meta property="og:image" content="[^"]*/images/([^"/]+)"', text)
        img = f"../images/{m.group(1)}" if m else "../images/favicon.jpg"
    return {"href": path.name, "title": post_title(path), "img": img}


def read_next_box(p: dict, ind: str) -> str:
    """Mid-article 'Also Read' card (styled by .also-read in blog-styles.css)."""
    t = p["title"]  # already HTML from the source <h1>
    alt = html.escape(re.sub(r"<[^>]+>", "", html.unescape(t)), quote=True)
    return (f'{ind}<div class="also-read">\n'
            f'{ind}    <span class="also-read-label">&#128073; Also Read</span>\n'
            f'{ind}    <div class="also-read-content">\n'
            f'{ind}        <img src="{p["img"]}" alt="{alt}" class="also-read-image" width="100" height="80" loading="lazy" decoding="async">\n'
            f'{ind}        <div class="also-read-text"><a href="{p["href"]}">{t}</a></div>\n'
            f'{ind}    </div>\n'
            f'{ind}</div>\n')


def insert_mid_reads(body: str, posts: list[dict], ind: str) -> str:
    """Drop Read Next boxes before the <h2> at ~1/3 and ~2/3 of the article,
    so readers get a hop to another post while they are still scrolling."""
    starts = [m.start() for m in re.finditer(r"<h2\b", body)]
    if len(starts) < 4 or not posts:
        return body
    picks = sorted({len(starts) // 3, (2 * len(starts)) // 3})
    for i, pos_idx in reversed(list(enumerate(picks))):
        if i < len(posts):
            at = starts[pos_idx]
            body = body[:at] + read_next_box(posts[i], ind) + "\n" + body[at:]
    return body


def read_next_grid(posts: list[dict], ind: str) -> str:
    """End-of-article 'Also Read' card grid + a button to the full blog list."""
    cards = "".join(
        f'{ind}    <a href="{p["href"]}" class="recent-post-card" style="text-decoration:none;">\n'
        f'{ind}        <img src="{p["img"]}" alt="{html.escape(re.sub(r"<[^>]+>", "", html.unescape(p["title"])), quote=True)}" width="400" height="140" loading="lazy" decoding="async">\n'
        f'{ind}        <div class="recent-post-content" style="text-align:left;"><h4>{p["title"]}</h4><span>Read now &rarr;</span></div>\n'
        f'{ind}    </a>\n' for p in posts)
    return (f"\n{ind}<h2>Also Read</h2>\n{ind}<div class=\"recent-posts-grid\">\n{cards}{ind}</div>\n"
            f'{ind}<p style="text-align:center;margin-top:1.5rem;">'
            f'<a href="../blogs.html" class="cta-btn" style="text-decoration:none;border:1px solid var(--blog-border);">'
            f'Browse all latest jobs &amp; updates &rarr;</a></p>\n')


def sidebar_recent(posts: list[dict]) -> str:
    ind = "                    "
    return "".join(
        f'{ind}<a href="{p["href"]}" class="sidebar-post">\n'
        f'{ind}    <img src="{p["img"]}" alt="{html.escape(re.sub(r"<[^>]+>", "", html.unescape(p["title"])), quote=True)}" width="200" height="133" loading="lazy" decoding="async">\n'
        f'{ind}    <div class="sidebar-post-content">\n{ind}        <h4>{p["title"]}</h4>\n{ind}    </div>\n'
        f'{ind}</a>\n' for p in posts).rstrip("\n")


def jsonld(obj: dict) -> str:
    return ('    <script type="application/ld+json">\n'
            + json.dumps(obj, indent=4, ensure_ascii=False) + "\n    </script>\n")


def assemble(meta: dict, body: str) -> str:
    site = CONFIG["site_url"].rstrip("/")
    slug, d = meta["slug"], today_ist()
    url, img = f"{site}/blogs/{slug}.html", f"{site}/images/{slug}.webp"
    job = meta.get("job") or {}
    faqs = [f for f in meta.get("faqs", []) if f.get("q") and f.get("a")]

    # JSON-LD
    blocks = [jsonld({
        "@context": "https://schema.org", "@type": "Article", "headline": meta["h1"][:110],
        "description": meta["meta_description"], "datePublished": d.isoformat(), "dateModified": d.isoformat(),
        "author": {"@type": "Organization", "name": "GetUpdated", "url": site},
        "publisher": {"@type": "Organization", "name": "GetUpdated",
                      "logo": {"@type": "ImageObject", "url": f"{site}/images/favicon.jpg"}},
        "image": img, "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "inLanguage": "en-IN", "isAccessibleForFree": True}),
        jsonld({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{site}/"},
            {"@type": "ListItem", "position": 2, "name": "Blogs", "item": f"{site}/blogs.html"},
            {"@type": "ListItem", "position": 3, "name": f"{meta['company']} {meta['role']}"[:80], "item": url}]})]
    jp = {"@context": "https://schema.org", "@type": "JobPosting", "title": job.get("title") or meta["role"],
          "description": job.get("description") or meta["meta_description"], "datePosted": d.isoformat(),
          "employmentType": job.get("employment_type") or "FULL_TIME",
          "hiringOrganization": {"@type": "Organization", "name": meta["company"],
                                 **({"sameAs": meta["company_url"]} if meta.get("company_url") else {})},
          "url": meta["official_url"], "directApply": False}
    if job.get("remote"):
        jp["jobLocationType"] = "TELECOMMUTE"
        jp["applicantLocationRequirements"] = {"@type": "Country", "name": "India"}
    else:
        jp["jobLocation"] = {"@type": "Place", "address": {"@type": "PostalAddress",
                             "addressLocality": job.get("city") or "India",
                             **({"addressRegion": job["region"]} if job.get("region") else {}),
                             "addressCountry": "IN"}}
    if job.get("qualifications"):
        jp["qualifications"] = job["qualifications"]
    if job.get("valid_through"):
        jp["validThrough"] = f"{job['valid_through']}T23:59:59"
    if int(job.get("vacancies") or 0) >= 100:  # small counts are never published
        jp["totalJobOpenings"] = job["vacancies"]
    blocks.append(jsonld(jp))
    if faqs:
        blocks.append(jsonld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in faqs]}))

    # article: intro + TOC + body + Also Read + FAQs
    ind = "                    "
    h2s = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<h2[^>]*>(.*?)</h2>", body, re.S)]
    toc = (f'{ind}<div class="info-box" id="table-of-contents">\n{ind}    <h2>Table of Contents</h2>\n{ind}    <ol>\n'
           + "".join(f"{ind}        <li>{h}</li>\n" for h in h2s + ["Also Read"] + (["FAQs"] if faqs else []))
           + f"{ind}    </ol>\n{ind}</div>\n")
    first = body.find("<h2")
    body = (body[:first] + "\n" + toc + "\n" + body[first:]) if first != -1 else toc + body
    recent = [post_info(REPO_DIR / p) for p in latest_posts(12) if not p.endswith(f"{slug}.html")]
    # newest previous posts go mid-article (most clicked), the next ones in the end grid
    body = insert_mid_reads(body, recent[:2], ind)
    body += read_next_grid(recent[2:6], ind)
    if faqs:
        body += f"\n{ind}<h2>FAQs</h2>\n" + "".join(
            f"{ind}<h2>{i}. {html.escape(f['q'])}</h2>\n{ind}<p>{html.escape(f['a'])}</p>\n\n"
            for i, f in enumerate(faqs, 1))
    tags = "\n".join(f'                    <a href="javascript:void(0)" class="tag">{html.escape(t)}</a>'
                     for t in meta.get("tags", [])[:6])

    page = (AUTO_DIR / "templates" / "post_template.html").read_text(encoding="utf-8")
    esc = lambda k: html.escape(str(meta.get(k, "")), quote=True)  # noqa: E731
    fills = {
        "{{META_DESCRIPTION}}": esc("meta_description"), "{{KEYWORDS}}": esc("keywords"),
        "{{SEO_TITLE}}": esc("seo_title"), "{{OG_TITLE}}": esc("og_title"),
        "{{OG_DESCRIPTION}}": esc("og_description"), "{{IMAGE_ALT}}": esc("image_alt"),
        "{{IMAGE_CAPTION}}": esc("image_caption"), "{{H1}}": esc("h1"),
        "{{DATE_ISO}}": d.isoformat(), "{{DATE_LONG}}": f"{d:%B} {d.day}, {d.year}",
        "{{URL}}": url, "{{IMAGE_URL}}": img, "{{SLUG}}": slug,
        "{{JSONLD}}": "".join(blocks).rstrip("\n"), "{{ARTICLE_CONTENT}}": body, "{{TAGS}}": tags,
        "{{RECENT_POSTS}}": sidebar_recent(recent[:8]),
    }
    for k, v in fills.items():
        page = page.replace(k, v)
    return page


# --------------------------------------------------------------------------- validation
REQUIRED_META = ["slug", "seo_title", "meta_description", "h1", "og_title", "og_description",
                 "card_title", "card_excerpt", "tag", "image_alt", "company", "role", "official_url",
                 "whatsapp_text", "faqs"]


def load_and_check(draft_dir: Path) -> tuple[dict, str]:
    meta_path, body_path = draft_dir / "meta.json", draft_dir / "body.html"
    if not meta_path.exists():
        raise ValueError("writer did not produce meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("verified") is not True:
        raise ValueError(f"not verified: {meta.get('verification_notes') or 'no reason given'}")
    missing = [k for k in REQUIRED_META if not meta.get(k)]
    if missing:
        raise ValueError(f"meta.json missing fields: {missing}")
    slug = meta["slug"]
    if not SLUG_RE.match(slug) or len(slug) > 110:
        raise ValueError(f"bad slug: {slug}")
    if (REPO_DIR / "blogs" / f"{slug}.html").exists():
        raise ValueError(f"blog already exists: {slug}")
    if not body_path.exists():
        raise ValueError("writer did not produce body.html")
    body = body_path.read_text(encoding="utf-8")
    problems = []
    if re.search(r"<(html|head|body|script|style)\b", body, re.I):
        problems.append("body.html contains page-level tags")
    if len(body) < 8000:
        problems.append(f"article too short ({len(body)} chars)")
    if len(re.findall(r"<h2", body)) < 7:
        problems.append("fewer than 7 <h2> sections")
    body = house_style(links_only_in_official_section(body), meta["official_url"])
    sec = official_section_bounds(body)
    if not sec:
        problems.append('no "Official Links" <h2> section')
    elif meta["official_url"] not in body[sec[0]:sec[1]]:
        problems.append("official apply/notification link missing from the Official Links section")
    if problems:
        raise ValueError("; ".join(problems))
    return meta, body


GREEN_LINK_STYLE = ("display:inline;color:#22c55e;text-decoration:none;background:transparent;border:none;"
                    "border-radius:0;padding:0;box-shadow:none;font-weight:700;letter-spacing:0.3px;")
URGENT_RE = re.compile(r"apply early|deadline|last date|closing date|closes|don.?t wait|do not wait|hurry|urgent", re.I)


def house_style(body: str, official_url: str) -> str:
    """Site look, applied in code (free): the official apply link is green (same inline style as
    the hand-written posts) and deadline / 'apply early' boxes are red (.alert-box)."""
    def green(m: re.Match) -> str:
        inner = m.group(2)
        return (f'<a href="{official_url}" target="_blank" rel="noopener" style="{GREEN_LINK_STYLE}">'
                f"{inner}</a>")
    body = re.sub(r'<a\s[^>]*href="(' + re.escape(official_url) + r')"[^>]*>(.*?)</a>', green, body, flags=re.S)

    def red(m: re.Match) -> str:
        if not URGENT_RE.search(m.group(2)):
            return m.group(0)
        return '<div class="alert-box">' + re.sub(r"fa-lightbulb|fa-circle-info|fa-info-circle", "fa-clock", m.group(2))
    return re.sub(r'<div class="(tip-box|warning-box|info-box)">(\s*<h4>.*?</h4>)', red, body, flags=re.S)


def official_section_bounds(body: str) -> tuple[int, int] | None:
    """Start/end offsets of the 'Official Links ...' <h2> section."""
    heads = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", body, re.S))
    for i, h in enumerate(heads):
        if re.search(r"official\s+link", h.group(1), re.I):
            return h.start(), (heads[i + 1].start() if i + 1 < len(heads) else len(body))
    return None


def links_only_in_official_section(body: str) -> str:
    """House rule: clickable external links live only in the Official Links section.
    Any external <a> elsewhere becomes plain <strong> text (free, deterministic)."""
    sec = official_section_bounds(body)
    if not sec:
        return body
    anchor = re.compile(r'<a\s[^>]*href="https?://[^"]*"[^>]*>(.*?)</a>', re.S | re.I)

    def to_text(chunk: str) -> str:
        return anchor.sub(lambda m: f"<strong>{re.sub(r'<i[^>]*></i>', '', m.group(1)).strip()}</strong>", chunk)

    return to_text(body[:sec[0]]) + body[sec[0]:sec[1]] + to_text(body[sec[1]:])


# a 1-2 digit number (not part of a bigger number like 1,265 or 3047) followed by posts/vacancies/...
SMALL_COUNT_RE = re.compile(r"(?<![\d,.])\b([1-9]\d?)\s*(\+\s*)?(posts?|vacanc\w*|openings?|seats?|positions?)\b", re.I)


def small_count_mentions(meta: dict, body: str) -> list[str]:
    """Counts below 100 that slipped into the text (house rule: don't publish them)."""
    hay = " ".join([body, meta.get("slug", "").replace("-", " ")] +
                   [str(meta.get(k, "")) for k in ("h1", "seo_title", "card_title", "card_excerpt",
                                                   "whatsapp_text", "meta_description", "og_title")] +
                   [str(f) for f in meta.get("key_facts", [])] + [str(f) for f in meta.get("faqs", [])])
    return sorted({m.group(0) for m in SMALL_COUNT_RE.finditer(re.sub(r"<[^>]+>", " ", hay))})


def validate_page(page: str) -> None:
    if "{{" in page:
        raise ValueError("unfilled template placeholder")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        json.loads(block)


def expand(draft_dir: Path, problem: str) -> None:
    """Ask the model to fix a rejected draft (too short / house rule), reusing the facts already gathered."""
    body = (draft_dir / "body.html").read_text(encoding="utf-8")
    meta = (draft_dir / "meta.json").read_text(encoding="utf-8")
    log(f"writer: expanding draft ({problem})")
    prompt = (f"The article draft was rejected: {problem}. Rewrite {draft_dir.as_posix()}/body.html "
              "with the Write tool so it has 9-13 <h2> sections and 1,500-2,200 words (12,000-18,000 characters). "
              "Keep every fact, link and class exactly as verified; add depth only with genuinely useful, "
              "non-factual guidance (preparation, what the work involves, how to apply well, mistakes to avoid). "
              "Do not invent facts. Only rewrite meta.json if the problem says so (keep it valid JSON with "
              "every field).\n\nMETA.JSON:\n" + meta + "\n\nCURRENT BODY.HTML:\n" + body)
    system = (AUTO_DIR / "prompts" / "writer_system.md").read_text(encoding="utf-8")
    run_claude(prompt, CONFIG["writer_model"], ["Write"], timeout=CONFIG["writer_timeout_sec"],
               effort=CONFIG.get("writer_effort"), system_prompt=system,
               budget_usd=CONFIG.get("writer_budget_usd"))


# --------------------------------------------------------------------------- main
def write_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise ValueError(f"unknown job {job_id}")
    with FileLock("git", wait=900):
        if not git_is_clean():
            raise RuntimeError("repo has uncommitted changes; refusing to run the writer")

    draft_dir = DRAFTS_DIR / job_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    for f in draft_dir.glob("*"):
        f.unlink()
    update_job(job_id, status="writing", started_at=now_iso())

    pre = prefetch(job)  # raises ValueError -> job fails cheaply, no model call
    log(f"writer: prefetched {len(pre['links'])} official link(s), {len(pre['excerpts'])} excerpt(s)")
    system = (AUTO_DIR / "prompts" / "writer_system.md").read_text(encoding="utf-8")
    try:
        run_claude(build_user_prompt(job, draft_dir, pre), CONFIG["writer_model"], WRITER_TOOLS,
                   timeout=CONFIG["writer_timeout_sec"], effort=CONFIG.get("writer_effort"),
                   system_prompt=system, budget_usd=CONFIG.get("writer_budget_usd"))
    finally:
        # Safety net: the writer must only write into the draft dir. Undo anything else.
        with FileLock("git", wait=900):
            if not git_is_clean():
                log("writer: model touched repo files; reverting them")
                run(["git", "reset", "--hard", "HEAD"])
                run(["git", "clean", "-fd", "--", "blogs", "images", "css", "js", "sections"])

    try:
        meta, body = load_and_check(draft_dir)
    except ValueError as exc:
        if not re.search(r"too short|fewer than 7", str(exc)):
            raise
        expand(draft_dir, str(exc))  # one cheap retry instead of discarding the research
        meta, body = load_and_check(draft_dir)
    small = small_count_mentions(meta, body)
    if small:
        expand(draft_dir, f"house rule broken: vacancy counts below 100 must not be mentioned, found {small}; "
                          "remove them from body.html AND rewrite meta.json without them")
        meta, body = load_and_check(draft_dir)
        if small_count_mentions(meta, body):
            meta["verification_notes"] = (meta.get("verification_notes", "") +
                                          f" | WARNING: still mentions {small_count_mentions(meta, body)}")
    meta["title"] = meta["h1"]  # publisher / approval message use "title"
    (draft_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    page = assemble(meta, body)
    validate_page(page)
    (draft_dir / "post.html").write_text(page, encoding="utf-8")
    # Post image: the company's own share image / logo; generated banner only as a fallback.
    img_pages = [meta.get("company_url"), meta.get("official_url")]
    if job.get("source_type") == "ats":
        img_pages.append(job["url"])
    if not company_image(draft_dir / "banner.webp", img_pages):
        log("writer: no company image found; using generated banner")
        make_banner(draft_dir / "banner.webp", meta["company"], meta["role"], meta["tag"],
                    meta.get("banner_extra", ""))
    update_job(job_id, status="draft_ready", slug=meta["slug"], draft_title=meta["h1"],
               drafted_at=now_iso(), drafted_on=today_ist().isoformat())
    log(f"writer: draft ready {meta['slug']}")
    return meta


if __name__ == "__main__":
    import sys
    print(write_job(sys.argv[1]))
