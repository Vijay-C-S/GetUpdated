"""Publisher: put an approved draft live and send the share pack to Telegram.

Steps: pull -> copy post + image -> add cards to index.html and blogs.html ->
add sitemap entry -> commit -> push -> wait until the URL is live -> Telegram.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import time

import requests

import telegram_api as tg
from common import (CONFIG, DRAFTS_DIR, REPO_DIR, FileLock, get_job, git_is_clean, log, now_iso,
                    run, today_ist, update_job)


CRLF, LF = chr(13) + chr(10), chr(10)


def read_site_file(path):
    """Read with normalised newlines; remember the file's own line ending."""
    raw = path.read_bytes().decode("utf-8")
    eol = CRLF if CRLF in raw else LF
    return raw.replace(CRLF, LF), eol


def write_site_file(path, text, eol):
    path.write_bytes(text.replace(LF, eol).encode("utf-8"))


def human_date(d) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def card_html(meta: dict, page: str) -> str:
    slug = meta["slug"]
    title = html.escape(meta["card_title"])
    excerpt = html.escape(meta["card_excerpt"])
    alt = html.escape(meta["image_alt"])
    date = human_date(today_ist())
    if page == "index":
        comment = f"<!-- Blog Post: {html.escape(meta['company'])} {html.escape(meta['role'])} -->"
        badge = '<span><i class="fas fa-fire" style="color: var(--danger);"></i> NEW!</span>'
    else:
        comment = f"<!-- {html.escape(meta['company'])} {html.escape(meta['role'])} -->"
        badge = f'<span class="blog-tag">{html.escape(meta["tag"])}</span>'
    return f"""                {comment}
                <a href="blogs/{slug}.html" class="blog-card clickable-card">
                    <div class="blog-image">
                        <img src="images/{slug}.webp"
                            alt="{alt}" style="object-fit: cover;" width="600"
                            height="400" loading="lazy" decoding="async">
                    </div>
                    <div class="blog-content">
                        <div class="blog-meta">
                            <span><i class="far fa-calendar"></i> {date}</span>
                            {badge}
                        </div>
                        <h3>{title}</h3>
                        <p>{excerpt}</p>
                        <span class="read-more">Read Article <i class="fas fa-arrow-right"></i></span>
                    </div>
                </a>
"""


def wire_index(meta: dict) -> None:
    path = REPO_DIR / "index.html"
    text, eol = read_site_file(path)
    anchor = '<div class="blogs-grid">'
    start = text.find(anchor)
    if start == -1:
        raise RuntimeError("index.html: blogs-grid not found")
    insert_at = start + len(anchor) + 1  # after the newline
    text = text[:insert_at] + card_html(meta, "index") + text[insert_at:]

    # keep only N cards in the homepage grid
    grid_start = text.find(anchor)
    grid_end = text.find('<div class="view-all">', grid_start)
    grid = text[grid_start:grid_end]
    card_re = re.compile(r'[ \t]*<!--[^>]*-->\s*<a href="blogs/[^"]+" class="blog-card[^"]*">.*?</a>\n?', re.S)
    cards = list(card_re.finditer(grid))
    keep = CONFIG["homepage_cards"]
    for m in reversed(cards[keep:]):
        grid = grid[:m.start()] + grid[m.end():]
    text = text[:grid_start] + grid + text[grid_end:]
    write_site_file(path, text, eol)


def wire_blogs_page(meta: dict) -> None:
    path = REPO_DIR / "blogs.html"
    text, eol = read_site_file(path)
    anchor = '<div class="all-blogs-grid" id="blogs-grid">'
    pos = text.find(anchor)
    if pos == -1:
        raise RuntimeError("blogs.html: all-blogs-grid not found")
    insert_at = pos + len(anchor) + 1
    text = text[:insert_at] + card_html(meta, "blogs") + "\n" + text[insert_at:]
    write_site_file(path, text, eol)


def wire_sitemap(meta: dict) -> None:
    path = REPO_DIR / "sitemap.xml"
    text, eol = read_site_file(path)
    site = CONFIG["site_url"].rstrip("/")
    today = today_ist().isoformat()
    loc = f"{site}/blogs/{meta['slug']}.html"
    entry = f"""  <url>
    <loc>{loc}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
    <image:image>
      <image:loc>{site}/images/{meta['slug']}.webp</image:loc>
      <image:title>{html.escape(meta.get('sitemap_image_title') or meta['title'])}</image:title>
    </image:image>
  </url>
"""
    blocks = list(re.finditer(r"  <url>\s*<loc>([^<]+)</loc>.*?</url>\n", text, re.S))
    blog_blocks = [b for b in blocks if "/blogs/" in b.group(1)]
    pos = None
    for b in blog_blocks:
        if b.group(1) > loc:
            pos = b.start()
            break
    if pos is None:
        pos = blog_blocks[-1].end() if blog_blocks else text.rfind("</urlset>")
    text = text[:pos] + entry + text[pos:]

    for page in (f"{site}/", f"{site}/blogs.html"):
        text = re.sub(rf"(<loc>{re.escape(page)}</loc>\s*<lastmod>)[^<]+(</lastmod>)",
                      rf"\g<1>{today}\g<2>", text, count=1)
    write_site_file(path, text, eol)


def wait_until_live(url: str, slug: str) -> bool:
    """GitHub Pages returns 404 until the new build is deployed."""
    deadline = time.time() + CONFIG["live_check_timeout_sec"]
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=20, params={"t": int(time.time())})
            if r.status_code == 200 and slug in r.text:
                return True
        except requests.RequestException:
            pass
        time.sleep(30)
    return False


def share_pack(meta: dict, url: str) -> str:
    text = meta["whatsapp_text"].strip()
    if url not in text:
        text += f"\n\n👉 Full details & apply link:\n{url}"
    return text


def publish(job_id: str) -> str:
    job = get_job(job_id)
    if not job or job.get("status") not in ("draft_ready", "publishing", "failed"):
        raise RuntimeError(f"job {job_id} is not ready to publish (status={job and job.get('status')})")
    draft = DRAFTS_DIR / job_id
    meta = json.loads((draft / "meta.json").read_text(encoding="utf-8"))
    slug = meta["slug"]
    url = f"{CONFIG['site_url'].rstrip('/')}/blogs/{slug}.html"

    with FileLock("git", wait=600):
        update_job(job_id, status="publishing")
        branch = CONFIG["git_branch"]
        if not git_is_clean():
            raise RuntimeError("repo has uncommitted changes; refusing to publish")
        run(["git", "checkout", branch])
        run(["git", "pull", "--rebase", "origin", branch])
        if (REPO_DIR / "blogs" / f"{slug}.html").exists():
            raise RuntimeError(f"blogs/{slug}.html already exists")

        shutil.copyfile(draft / "post.html", REPO_DIR / "blogs" / f"{slug}.html")
        shutil.copyfile(draft / "banner.webp", REPO_DIR / "images" / f"{slug}.webp")
        try:
            wire_index(meta)
            wire_blogs_page(meta)
            wire_sitemap(meta)
            paths = [f"blogs/{slug}.html", f"images/{slug}.webp", "index.html", "blogs.html", "sitemap.xml"]
            run(["git", "add", "--", *paths])
            msg = (f"Add {meta['company']} {meta['role']} blog (auto)\n\n"
                   f"Source: {meta['official_url']}\nGenerated by the GetUpdated automation pipeline.")
            run(["git", "commit", "-m", msg])
        except Exception:
            run(["git", "reset", "--hard", "HEAD"])
            run(["git", "clean", "-fd", "--", "blogs", "images"])
            raise
        try:
            run(["git", "push", "origin", branch], timeout=120)
        except RuntimeError:
            run(["git", "pull", "--rebase", "origin", branch])
            run(["git", "push", "origin", branch], timeout=120)
        sha = run(["git", "rev-parse", "--short", "HEAD"]).strip()

    update_job(job_id, status="published", url=url, commit=sha, published_at=now_iso(),
               published_on=today_ist().isoformat())
    log(f"publisher: pushed {slug} ({sha}); waiting for it to go live")

    live = wait_until_live(url, slug)
    pack = share_pack(meta, url)
    note = "" if live else "\n\n⚠️ Not live yet after waiting; GitHub Pages may still be building."
    tg.send_photo(draft / "banner.webp", caption=f"✅ Published: {meta['title']}{note}")
    tg.send_message(pack)  # plain text: long-press -> copy -> paste into WhatsApp channel
    return url


if __name__ == "__main__":
    import sys
    print(publish(sys.argv[1]))
