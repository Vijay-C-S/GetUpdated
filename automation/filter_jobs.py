"""Filter: Haiku scores raw candidates and queues the ones worth a blog post."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from claude_cli import extract_json, run_claude
from common import (AUTO_DIR, CONFIG, IST, FileLock, existing_blog_slugs, load_jobs, log, now_iso,
                    save_jobs)


def expire_stale(hours: int = 48) -> None:
    """Candidates nobody reviewed within `hours` are old news; drop them."""
    cutoff = (datetime.now(IST) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with FileLock("jobs", max_age=120, wait=60):
        jobs = load_jobs()
        for j in jobs.values():
            if j.get("status") == "candidate" and j.get("found_at", "") < cutoff:
                j.update(status="rejected", reason="stale (not reviewed in time)", updated_at=now_iso())
        save_jobs(jobs)


def filter_candidates() -> int:
    expire_stale()
    jobs = load_jobs()
    cands = [j for j in jobs.values() if j.get("status") == "candidate"]
    if not cands:
        log("filter: no candidates")
        return 0
    cands = sorted(cands, key=lambda j: j.get("found_at", ""), reverse=True)[: CONFIG["max_candidates_per_filter"]]

    recent_titles = [j["title"] for j in jobs.values()
                     if j.get("status") in ("queued", "writing", "draft_ready", "published")][-60:]
    # Compact payload: one short line per candidate keeps input tokens low.
    lines = []
    for j in cands:
        note = (j.get("summary") or "")[:140].replace("\n", " ")
        lines.append(f'{j["id"]} | {j["title"][:160]} | src: {j["source"].split(" (")[0]}'
                     + (f" | note: {note}" if note else ""))
    covered = recent_titles + existing_blog_slugs()[-60:]
    prompt = ("ALREADY COVERED:\n" + "\n".join(f"- {t}" for t in covered)
              + "\n\nCANDIDATES (id | title | src | note):\n" + "\n".join(lines))
    system = (AUTO_DIR / "prompts" / "filter.md").read_text(encoding="utf-8")

    reply = run_claude(prompt, CONFIG["filter_model"], tools=[], system_prompt=system,
                       timeout=CONFIG["filter_timeout_sec"], budget_usd=CONFIG.get("filter_budget_usd"))
    verdicts = {v["id"]: v for v in extract_json(reply) if isinstance(v, dict) and "id" in v}

    queued = 0
    with FileLock("jobs", max_age=120, wait=60):
        jobs = load_jobs()
        for c in cands:
            v = verdicts.get(c["id"])
            job = jobs[c["id"]]
            if not v:
                continue  # stays candidate; retried next run
            score = int(v.get("score", 0) or 0)
            ok = bool(v.get("worthy")) and score >= CONFIG["min_score"]
            job.update({
                "status": ("lead_pending" if CONFIG.get("require_lead_approval", True) else "queued") if ok else "rejected",
                "score": score, "reason": v.get("reason", ""),
                "company": v.get("company", ""), "role": v.get("role", ""),
                "category": v.get("category", ""), "updated_at": now_iso(),
            })
            queued += ok
        save_jobs(jobs)
    log(f"filter: {queued} queued / {len(cands)} reviewed")
    return queued


if __name__ == "__main__":
    filter_candidates()
