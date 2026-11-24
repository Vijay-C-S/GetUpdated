"""Writer: Sonnet verifies a queued job and writes the article; Python builds the page.

Token savers:
- Python pre-fetches the lead page and official HTML pages. PDFs are never read (links only);
  so the model needs few or no web calls; leads with no official link are dropped before
  any model call.
- The model writes only body.html (article) + meta.json. The head, SEO tags, JSON-LD,
  navbar, TOC, Also Read, FAQ, sidebar and footer are rendered here from templates/.
- Static instructions live in the system prompt (cache-friendly across jobs).

Output: automation/state/drafts/<id>/{post.html, meta.json, banner.webp}
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import rrb
from banner import company_logo, make_banner
from claude_cli import run_claude
from gumd import to_html
from common import (AUTO_DIR, CONFIG, DRAFTS_DIR, REPO_DIR, FileLock, get_job,
                    git_is_clean, log, now_iso, run, today_ist, update_job)

WEB_TOOLS = ["WebFetch", "WebSearch"]  # loaded only when too little page text was pre-fetched
ENOUGH_TEXT = 1500  # chars of pre-fetched lead/official text that make web tools unnecessary
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


def is_pdf(url: str) -> bool:
    return url.lower().split("?")[0].split("#")[0].endswith(".pdf")


class NeedsOwnerConfirm(Exception):
    """The writer would drop this job: ask the owner once on Telegram instead of skipping silently.
    kind: 'unconfirmed' (no official source) or 'rejected' (writer thinks closed / not entry level / ...)."""

    def __init__(self, reason: str, kind: str = "unconfirmed"):
        super().__init__(reason)
        self.kind = kind


def not_official_link(url: str, job: dict, known: set[str]) -> bool:
    """official_url points at a job site (FreeJobAlert etc.), at the job-site article the lead came from,
    or is just a homepage nobody gave us (no real apply link). The owner is asked for the real link
    before the draft is shown. known = official links found by prefetch (trusted)."""
    if (url or "").rstrip("/") in known:
        return False
    dom = _domain(url or "")
    if not dom or any(g in dom for g in AGGREGATORS):
        return True
    if urlparse(url).path.strip("/") == "":  # bare homepage = writer fell back, no apply link known
        return True
    job_site_lead = not (job.get("official") or job.get("source_type") in ("ats", "backfill", "manual", "email")
                         or job.get("source", "").startswith("manual"))
    return job_site_lead and dom == _domain(job.get("url") or "")


def fetch_text(url: str, limit: int = 3500) -> str:
    """Readable text of an HTML page. PDFs are never downloaded or read (too many tokens): they are
    passed to the writer as links only. Raises on failure."""
    if is_pdf(url):
        raise ValueError("PDF: link only, not read")
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    if "pdf" in r.headers.get("content-type", "").lower():
        raise ValueError("PDF: link only, not read")
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


def _rrb_lead(job: dict) -> bool:
    """Railway Recruitment Board lead (not IBPS 'RRB' = Regional Rural Banks)."""
    if rrb.is_rrb_url(job.get("url", "")):
        return True
    t = " ".join(str(job.get(k) or "") for k in ("title", "summary", "forwarded_text", "manual_note")).lower()
    if "ibps" in t or "rural bank" in t:
        return False
    return "railway recruitment" in t or bool(rrb.cen_key(t)) or bool(re.search(r"\brrb\b", t))


def _add_rrb_notices(pre: dict, job: dict) -> None:
    """rrbapply.gov.in is a JS app the fetchers can't read; its public notice list (small JSON) confirms
    the CEN exists and gives the official dates + PDF link. The PDF itself is not read."""
    text = " ".join(str(job.get(k) or "") for k in ("title", "url", "summary", "forwarded_text", "manual_note"))
    try:
        found = rrb.match(text, rrb.notices())
    except Exception as exc:  # noqa: BLE001
        log(f"writer: RRB notice list failed: {exc}")
        return
    if not found:
        return
    listing = "\n".join(f"- {n['title']} | apply {n['start']} to {n['end']} | notification PDF: {n['url']}"
                        for n in found)
    pre["links"] = ([(n["url"], f"official RRB notification PDF: {n['title']}") for n in found]
                    + [(rrb.PORTAL, "official RRB apply portal")] + pre["links"])
    pre["excerpts"].insert(0, (rrb.WHATS_NEW + " (official RRB apply portal notice list)", listing))


def prefetch(job: dict) -> dict:
    """Return {'lead_text', 'links', 'excerpts'}. Raises NeedsOwnerConfirm when a job-site lead has no
    official link (you confirm on Telegram), ValueError when the lead page itself can't be read."""
    pre = _prefetch_lead(job)
    if job.get("owner_link"):  # the official link you sent on Telegram: the top fact source
        url = job["owner_link"]
        pre["links"] = [(url, "official link given by the owner")] + [l for l in pre["links"] if l[0] != url]
        if not is_pdf(url):
            try:
                txt = fetch_text(url, 4000)
                if len(txt) > 200:
                    pre["excerpts"].insert(0, (url + " (OFFICIAL LINK GIVEN BY THE OWNER)", txt))
            except Exception as exc:  # noqa: BLE001
                log(f"writer: owner link {url} could not be read: {exc}")
    return pre


def _prefetch_lead(job: dict) -> dict:
    if not _rrb_lead(job):
        return _prefetch(job)
    try:
        pre = _prefetch(job)
    except Exception as exc:  # noqa: BLE001  (lead page had nothing usable; the RRB API may)
        log(f"writer: prefetch {job.get('url')} failed, trying RRB notices: {exc}")
        pre = {"lead_text": "", "links": [], "excerpts": []}
    _add_rrb_notices(pre, job)
    return pre


def _prefetch(job: dict) -> dict:
    if job.get("source_type") == "ats":
        return {"lead_text": "", "links": [(job["url"], "official posting")],
                "excerpts": [(job["url"], job.get("full_text") or fetch_text(job["url"], 5000))]}
    if job.get("source_type") == "backfill":  # found by web research; many govt sites refuse the VM
        pre = {"lead_text": "", "links": [(job["url"], "official notice (found by backfill research)")], "excerpts": []}
        try:
            txt = fetch_text(job["url"], 5000)
            if len(txt) > 200:
                pre["excerpts"].append((job["url"], txt))
        except Exception as exc:  # noqa: BLE001
            log(f"writer: prefetch {job['url']} failed: {exc}")
        return pre
    if job.get("official"):  # official govt/bank/PSU page or PDF: the lead itself is the source
        pre = {"lead_text": "", "links": [(job["url"], "official notice")], "excerpts": []}
        if not is_pdf(job["url"]):  # a PDF stays a link: the writer reads a job site's summary instead
            try:
                pre["excerpts"].append((job["url"], fetch_text(job["url"], 5000)))
            except Exception as exc:  # noqa: BLE001
                log(f"writer: prefetch {job['url']} failed: {exc}")
        return pre
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
            for href, _label in [x for x in pre["links"] if not is_pdf(x[0])][:3]:
                try:
                    txt = fetch_text(href, 3000)
                    if len(txt) > 200:
                        pre["excerpts"].append((href, txt))
                        break
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log(f"writer: prefetch {job['url']} failed: {exc}")
        return pre
    # Job-site lead (FreeJobAlert, FreshersNow, ...): its text is the fact source. Official links are
    # collected; only one official HTML page is read (to confirm), PDFs are never read.
    try:
        r = requests.get(job["url"], headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception:
        if job.get("owner_override"):  # you said write it: the writer searches for the job itself
            return {"lead_text": "", "links": [], "excerpts": []}
        raise
    lead_text = fetch_text(job["url"], 5000)
    links = official_links(r.text, job["url"])
    if not links and not job.get("owner_confirmed"):
        raise NeedsOwnerConfirm("the job site gives no official link")
    excerpts = []
    for href, _label in [x for x in links if not is_pdf(x[0])][:3]:
        try:
            txt = fetch_text(href, 3000)
            if len(txt) > 200:
                excerpts.append((href, txt))
                break
        except Exception as exc:  # noqa: BLE001
            log(f"writer: prefetch {href} failed: {exc}")
    return {"lead_text": lead_text, "links": links, "excerpts": excerpts}


# --------------------------------------------------------------------------- prompt
def owner_confirmed(job: dict) -> bool:
    """You tapped ✅ 'Exists' on Telegram, or sent the lead yourself (/post, forward)."""
    return bool(job.get("owner_confirmed")) or job.get("source", "").startswith("manual")


def prefetched_chars(pre: dict) -> int:
    return len(pre["lead_text"]) + sum(len(t) for _, t in pre["excerpts"])


def build_user_prompt(job: dict, pre: dict) -> str:
    d = today_ist()
    parts = [
        f"TODAY: {d.isoformat()} (IST)",
        "LEAD: " + json.dumps({k: job.get(k) for k in ("title", "url", "source", "summary", "company",
                                                        "role", "category", "manual_note") if job.get(k)},
                              ensure_ascii=False),
    ]
    if job.get("source_hint"):
        parts.append(f"HINT: {job['source_hint']}")
    if job.get("forwarded_text"):
        parts.append("POST THE OWNER FORWARDED (from a WhatsApp/Telegram channel; use it to identify the job "
                     "and as a fact source if no job-site/official text is given):\n" + job["forwarded_text"][:1500])
    if owner_confirmed(job):
        parts.append("OWNER CONFIRMED: the site owner has checked that this job is real and open. Do NOT mark it "
                     "unverified for lack of an official page; write it from the text given here (or one job-site "
                     "article found with WebSearch). Use the official link if one is known, else the organisation's "
                     "official website as official_url.")
    if job.get("owner_link"):
        parts.append(f"OFFICIAL LINK FROM THE OWNER: {job['owner_link']}\nThe owner found this official apply / "
                     "notification link. Use it as official_url and as the apply link in How to Apply and Official "
                     "Links. If it is a PDF, only link it (never open it) and take the facts from the other text.")
    if job.get("owner_override"):
        parts.append("OWNER OVERRIDE: an earlier run wanted to skip this job"
                     + (f" ({job['override_reason'][:300]})" if job.get("override_reason") else "")
                     + ". The owner checked it and said WRITE IT. Do NOT write verified:false for any reason "
                     "(closed, not entry level, experience, unconfirmed): write the full post with verified:true "
                     "from the best text available, state dates exactly as the sources give them, and if some "
                     "posts need experience, say so clearly in the eligibility section.")
    if pre["lead_text"]:
        parts.append("JOB-SITE PAGE TEXT (fact source; prefer official text where both are given):\n" + pre["lead_text"])
    if pre["links"]:
        parts.append("OFFICIAL LINKS (for the Official Links section; PDFs are NOT to be opened):\n"
                     + "\n".join(f"- {h} ({l})" for h, l in pre["links"]))
    for href, txt in pre["excerpts"]:
        parts.append(f"OFFICIAL PAGE EXCERPT from {href}:\n{txt}")
    if prefetched_chars(pre) < ENOUGH_TEXT:
        parts.append("Too little page text was pre-fetched: WebSearch for ONE job-site article (e.g. freejobalert, "
                     "freshersnow, sarkari sites) or the official HTML page, and read it. Never open PDFs.")
    else:
        parts.append("You have no web tools: write from the text above.")
    return "\n\n".join(parts)


def save_reply(draft_dir: Path, reply: str, need_meta: bool = True) -> None:
    """The model answers in ONE message: ===META=== {json} ===BODY=== GU-markdown (no Write tool:
    fewer turns, no JSON-escaped HTML). Saves meta.json, body.md and body.html (house HTML)."""
    m = re.search(r"===\s*META\s*===(.*?)(?====\s*BODY\s*===|$)", reply, re.S)
    b = re.search(r"===\s*BODY\s*===(.*)$", reply, re.S)
    if m:
        raw = m.group(1)
        meta = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        (draft_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    elif need_meta:
        raise ValueError("writer reply has no ===META=== block: " + reply[:300])
    if b:
        md = re.sub(r"^```\w*\s*|\s*```$", "", b.group(1).strip())
        (draft_dir / "body.md").write_text(md, encoding="utf-8")
        (draft_dir / "body.html").write_text(to_html(md), encoding="utf-8")


def fill_derived_meta(meta: dict) -> dict:
    """Fields the model no longer writes (saves output tokens): copied from the ones it does."""
    meta.setdefault("og_title", meta.get("seo_title"))
    meta.setdefault("og_description", meta.get("meta_description"))
    meta.setdefault("card_title", meta.get("seo_title"))
    meta.setdefault("image_alt", meta.get("seo_title") or meta.get("h1"))
    meta.setdefault("image_caption", f"{meta.get('company', '')} {meta.get('role', '')} {today_ist().year}".strip())
    meta.setdefault("sitemap_image_title", meta.get("seo_title"))
    return meta


def free_slug(slug: str) -> str:
    """The model no longer gets the list of used slugs (saves ~2k input tokens): de-clash here."""
    base, n = slug, 2
    while (REPO_DIR / "blogs" / f"{slug}.html").exists():
        slug, n = f"{base[:105]}-{n}", n + 1
    return slug


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
REQUIRED_META = ["slug", "seo_title", "meta_description", "h1", "card_excerpt", "tag", "company", "role",
                 "official_url", "whatsapp_text", "faqs"]


def _read_meta(draft_dir: Path) -> dict:
    try:
        return json.loads((draft_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_and_check(draft_dir: Path) -> tuple[dict, str]:
    meta_path, body_path = draft_dir / "meta.json", draft_dir / "body.html"
    if not meta_path.exists():
        raise ValueError("writer did not produce meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("verified") is not True:
        raise ValueError(f"not verified: {meta.get('verification_notes') or 'no reason given'}")
    fill_derived_meta(meta)
    if SLUG_RE.match(str(meta.get("slug") or "")):
        meta["slug"] = free_slug(meta["slug"])
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
                                                   "whatsapp_text", "meta_description", "og_title", "banner_chips")] +
                   [str(f) for f in meta.get("key_facts", [])] + [str(f) for f in meta.get("faqs", [])])
    return sorted({m.group(0) for m in SMALL_COUNT_RE.finditer(re.sub(r"<[^>]+>", " ", hay))})


def validate_page(page: str) -> None:
    if "{{" in page:
        raise ValueError("unfilled template placeholder")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        json.loads(block)


def expand(draft_dir: Path, problem: str) -> None:
    """Ask the model to fix a rejected draft (too short / house rule), reusing the facts already gathered."""
    md_path = draft_dir / "body.md"
    body = (md_path if md_path.exists() else draft_dir / "body.html").read_text(encoding="utf-8")
    meta = (draft_dir / "meta.json").read_text(encoding="utf-8")
    log(f"writer: expanding draft ({problem})")
    prompt = (f"The article draft was rejected: {problem}. Reply with ===BODY=== and the full rewritten article "
              "(same markup) with 9-13 ## sections and 1,500-2,200 words. Keep every fact and link exactly as "
              "verified; add depth only with genuinely useful, non-factual guidance (preparation, what the work "
              "involves, how to apply well, mistakes to avoid). Do not invent facts. Put a ===META=== block "
              "(complete JSON) before ===BODY=== ONLY if the problem says meta.json must change."
              "\n\nMETA.JSON:\n" + meta + "\n\nCURRENT ARTICLE:\n" + body)
    system = (AUTO_DIR / "prompts" / "writer_system.md").read_text(encoding="utf-8")
    reply = run_claude(prompt, CONFIG["writer_model"], [], timeout=CONFIG["writer_timeout_sec"],
                       effort=CONFIG.get("writer_effort"), system_prompt=system,
                       budget_usd=CONFIG.get("writer_budget_usd"))
    save_reply(draft_dir, reply, need_meta=False)


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

    pre = prefetch(job)  # ValueError -> job fails cheaply; NeedsOwnerConfirm -> asks you; no model call
    log(f"writer: prefetched {len(pre['links'])} official link(s), {len(pre['excerpts'])} excerpt(s)")
    system = (AUTO_DIR / "prompts" / "writer_system.md").read_text(encoding="utf-8")
    tools = [] if prefetched_chars(pre) >= ENOUGH_TEXT else WEB_TOOLS
    try:
        reply = run_claude(build_user_prompt(job, pre), CONFIG["writer_model"], tools,
                           timeout=CONFIG["writer_timeout_sec"], effort=CONFIG.get("writer_effort"),
                           system_prompt=system, budget_usd=CONFIG.get("writer_budget_usd"))
        save_reply(draft_dir, reply)
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
        m = _read_meta(draft_dir)
        if m.get("verified") is False and not job.get("owner_override"):
            # Never skip on the writer's word alone: ask you once (Write anyway / Skip).
            unconfirmed = bool(m.get("unconfirmed"))
            raise NeedsOwnerConfirm(m.get("verification_notes") or "no reason given",
                                    "unconfirmed" if unconfirmed else "rejected") from exc
        if not re.search(r"too short|fewer than 7", str(exc)):
            raise
        expand(draft_dir, str(exc))  # one cheap retry instead of discarding the research
        meta, body = load_and_check(draft_dir)
    known = {h.rstrip("/") for h, _ in pre["links"]}
    if not job.get("owner_link") and not_official_link(meta["official_url"], job, known):
        # The apply link is someone else's blog (or only a homepage): keep the draft, ask you first.
        raise NeedsOwnerConfirm(f"the apply link is not an official apply page: {meta['official_url']}", "link")
    return finalize(job, draft_dir, meta, body)


def finish_with_link(job_id: str) -> dict:
    """You answered the 'apply link is not official' question: put your link (or keep the old one if you
    said use it) into the draft that is already written, and finish the page. No model call."""
    job = get_job(job_id)
    draft_dir = DRAFTS_DIR / job_id
    meta, body = load_and_check(draft_dir)
    old, new = meta["official_url"], job.get("owner_link")
    if new and new != old:
        body = body.replace(old, new).replace(html.escape(old, quote=True), new)
        meta = json.loads(json.dumps(meta, ensure_ascii=False).replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]))
        meta["official_url"] = new
        (draft_dir / "body.html").write_text(body, encoding="utf-8")
        (draft_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        meta, body = load_and_check(draft_dir)
    return finalize(job, draft_dir, meta, body)


def banner_chips(meta: dict) -> list[str]:
    """Up to 3 short facts for the banner: the writer's banner_chips, else built from the job data."""
    chips = [str(c).strip() for c in meta.get("banner_chips") or [] if str(c).strip() and len(str(c)) <= 26]
    if chips:
        return [c for c in chips if not SMALL_COUNT_RE.search(c)][:3]
    job = meta.get("job") or {}
    if int(job.get("vacancies") or 0) >= 100:
        chips.append(f"{int(job['vacancies']):,} posts")
    chips += [p.strip() for p in str(meta.get("banner_extra") or "").split("•") if 0 < len(p.strip()) <= 26]
    return chips[:3]


def banner_deadline(meta: dict) -> str:
    """'12 Dec 2026' from job.valid_through, '' if none or already past."""
    from datetime import date
    try:
        d = date.fromisoformat(str((meta.get("job") or {}).get("valid_through") or "")[:10])
    except ValueError:
        return ""
    return f"{d.day} {d:%b %Y}" if d >= today_ist() else ""


def finalize(job: dict, draft_dir: Path, meta: dict, body: str) -> dict:
    job_id = draft_dir.name
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
    # Post image: designed banner (colours/icon by post type, key facts, last date) with the company logo.
    img_pages = [meta.get("company_url"), meta.get("official_url")]
    if job.get("source_type") == "ats":
        img_pages.append(job["url"])
    logo = company_logo(img_pages, [*(meta.get("logo_names") or []), meta["company"]])
    log(f"writer: banner {'with company logo' if logo else 'with icon (no logo found)'}")
    make_banner(draft_dir / "banner.webp", meta["company"], meta["role"], meta["tag"], meta.get("banner_extra", ""),
                chips=banner_chips(meta), deadline=banner_deadline(meta), logo=logo)
    update_job(job_id, status="draft_ready", slug=meta["slug"], draft_title=meta["h1"],
               drafted_at=now_iso(), drafted_on=today_ist().isoformat())
    log(f"writer: draft ready {meta['slug']}")
    return meta


if __name__ == "__main__":
    import sys
    print(write_job(sys.argv[1]))
