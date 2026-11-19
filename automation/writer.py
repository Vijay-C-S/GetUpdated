"""Writer: Sonnet researches a queued job and writes a draft post in the site's format.

The draft (post.html + meta.json + banner.webp) lives in automation/state/drafts/<id>/.
Nothing in the live site is touched here; publisher.py does that after approval.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from banner import make_banner
from claude_cli import run_claude
from common import (AUTO_DIR, CONFIG, DRAFTS_DIR, REPO_DIR, FileLock, existing_blog_slugs, get_job,
                    git_is_clean, log, now_iso, run, today_ist, update_job)

WRITER_TOOLS = ["Read", "Glob", "Grep", "WebFetch", "WebSearch", "Write", "Edit"]
REQUIRED_META = ["slug", "title", "card_title", "card_excerpt", "tag", "image_alt",
                 "company", "role", "official_url", "whatsapp_text", "verified"]
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def latest_posts(n: int = 3) -> list[str]:
    """Most recently added blog files according to git history."""
    out = run(["git", "log", "--diff-filter=A", "--name-only", "--format=", "-n", "40", "--", "blogs/"])
    seen = []
    for line in out.splitlines():
        line = line.strip()
        if line.endswith(".html") and line not in seen and (REPO_DIR / line).exists():
            seen.append(line)
        if len(seen) == n:
            break
    return seen


def build_prompt(job: dict, draft_dir: Path) -> str:
    tpl = (AUTO_DIR / "prompts" / "writer.md").read_text(encoding="utf-8")
    d = today_ist()
    repl = {
        "{{JOB}}": json.dumps({k: job.get(k) for k in ("title", "url", "summary", "source", "company",
                                                         "role", "category", "manual_note")},
                              indent=1, ensure_ascii=False),
        "{{TODAY}}": d.isoformat(),
        "{{TODAY_HUMAN}}": f"{d:%b} {d.day}, {d.year}",
        "{{DRAFT_DIR}}": draft_dir.as_posix(),
        "{{TEMPLATES}}": "\n".join(f"- {p}" for p in latest_posts()),
        "{{SITE}}": CONFIG["site_url"],
        "{{EXISTING_SLUGS}}": ", ".join(sorted(existing_blog_slugs())[-400:]),
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


def validate_draft(draft_dir: Path) -> dict:
    meta_path, post_path = draft_dir / "meta.json", draft_dir / "post.html"
    if not meta_path.exists():
        raise ValueError("writer did not produce meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("verified") is not True:
        raise ValueError(f"not verified: {meta.get('verification_notes') or meta.get('reason') or 'no reason given'}")
    missing = [k for k in REQUIRED_META if not meta.get(k) and meta.get(k) is not False]
    if missing:
        raise ValueError(f"meta.json missing fields: {missing}")
    slug = meta["slug"]
    if not SLUG_RE.match(slug) or len(slug) > 110:
        raise ValueError(f"bad slug: {slug}")
    if (REPO_DIR / "blogs" / f"{slug}.html").exists():
        raise ValueError(f"blog already exists: {slug}")
    if not post_path.exists():
        raise ValueError("writer did not produce post.html")
    html = post_path.read_text(encoding="utf-8")
    problems = []
    if len(html) < 15000:
        problems.append(f"post too short ({len(html)} bytes)")
    if "</html>" not in html or "<title>" not in html:
        problems.append("incomplete HTML document")
    if f"blogs/{slug}.html" not in html:
        problems.append("canonical/og:url does not use the slug")
    if f"images/{slug}.webp" not in html:
        problems.append(f"image path images/{slug}.webp not referenced")
    if len(re.findall(r"<h2", html)) < 8:
        problems.append("fewer than 8 <h2> sections")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            json.loads(block)
        except json.JSONDecodeError as exc:
            problems.append(f"invalid JSON-LD: {exc}")
    if "googletagmanager" not in html or "adsbygoogle" not in html:
        problems.append("analytics/adsense head block missing")
    if problems:
        raise ValueError("; ".join(problems))
    return meta


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

    try:
        run_claude(build_prompt(job, draft_dir), CONFIG["writer_model"], WRITER_TOOLS,
                   timeout=CONFIG["writer_timeout_sec"])
    finally:
        # Safety net: the writer must only write into the draft dir. Undo anything else.
        # Hold the git lock so we never revert a publish that is in progress.
        with FileLock("git", wait=900):
            if not git_is_clean():
                log("writer: model touched repo files; reverting them")
                run(["git", "reset", "--hard", "HEAD"])
                run(["git", "clean", "-fd", "--", "blogs", "images", "css", "js", "sections"])

    meta = validate_draft(draft_dir)
    make_banner(draft_dir / "banner.webp", meta["company"], meta["role"], meta["tag"],
                meta.get("banner_extra", ""))
    update_job(job_id, status="draft_ready", slug=meta["slug"], draft_title=meta["title"],
               drafted_at=now_iso(), drafted_on=today_ist().isoformat())
    log(f"writer: draft ready {meta['slug']}")
    return meta


if __name__ == "__main__":
    import sys
    print(write_job(sys.argv[1]))
