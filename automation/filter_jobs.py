"""Filter: Haiku scores raw candidates and queues the ones worth a blog post."""
from __future__ import annotations

import json
import re
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


# ---------------------------------------------------------------- free pre-filter (no tokens)
# Only obvious cases. Anything unsure still goes to the AI.
NOT_A_VACANCY = re.compile(r"time ?table|shortlist|selection list|merit list|final list|provisional list|test date|"
                           r"exam date|interview (date|schedule)|call letter|cut ?off|syllabus|exam pattern|"
                           r"result|admit card|answer key|hall ticket|score ?card|download .* (list|marks)", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|chief|general manager|deputy general|additional general|director|"
                    r"head|principal|technical lead|team lead|lead\b|vice president|vp)\b", re.I)
# HR freshers are published too, but only for Bengaluru or remote jobs, so HR words are not in NON_IT.
HR_ROLE = re.compile(r"\b(hr|human resources?|recruit\w*|talent acquisition|people (ops|operations|team|partner)|"
                     r"payroll)\b", re.I)
BENGALURU_OR_REMOTE = re.compile(r"\b(bengaluru|bangalore|remote|work from home|wfh|anywhere|home[ -]based)\b", re.I)
NON_IT = re.compile(r"\b(talent|sales|sdr|bdr|business development|"
                    r"marketing|seo|content|social media|copywrit\w*|procurement|purchase|customer success|"
                    r"compliance|legal|finance|accounting|accountant|strategy|operations associate|"
                    r"hvac|mep|civil|mechanical|architect(ure)? intern|interior|fashion|nursing|pharma\w*)\b", re.I)
FOREIGN_ONLY = re.compile(r"\((usa|us|uk|canada|europe|emea|ireland|germany|japan|australia)\)|remote[ ,-]+"
                          r"(us|usa|united states|uk|canada|europe|emea|ireland|germany|brazil|mexico|australia)\b|"
                          r"\b(san francisco|new york|nyc|seattle|london|dublin|toronto|foster city)\b", re.I)
OTHER_STATE = re.compile(r"\b(andhra|telangana|tamil ?nadu|kerala|maharashtra|gujarat|rajasthan|punjab|haryana|"
                         r"himachal|uttarakhand|uttar pradesh|bihar|jharkhand|odisha|orissa|west bengal|assam|"
                         r"meghalaya|manipur|mizoram|nagaland|tripura|arunachal|sikkim|goa|madhya pradesh|"
                         r"chhattisgarh|jammu|kashmir|ladakh|puducherry|chandigarh|delhi|appsc|tspsc|tnpsc|mpsc|"
                         r"rpsc|bpsc|uppsc|opsc|wbpsc|gpsc|hpsc|ukpsc|jkpsc|osssc|dsssb|hssc|upsssc|"
                         r"district court|collector|dlsa|zilla|panchayat|dm office)\b", re.I)
CENTRAL = re.compile(r"\b(iit|nit|iiit|iim|iisc|iiser|aiims|icmr|csir|icar|isro|drdo|rrb|railway|bank|ssc|upsc|"
                     r"ibps|central|indian|national|bharat|ntpc|nhpc|ongc|bel|hal|bsnl|esic|epfo|kvs|nvs|army|"
                     r"navy|air force|coast guard|bsf|crpf|cisf|itbp|aai|fci|lic|nabard|rbi|sbi|sidbi|nic|"
                     r"c-?dac|ecil|cdot|c-dot|beml|karnataka|bengaluru|bangalore)\b", re.I)


ENTRY_HINT = re.compile(r"\b(assistant|trainee|junior|apprentice|clerk|intern|graduate|fresher|technician|"
                        r"multi tasking|mts|constable|probationary)", re.I)
BIG_COUNT = re.compile(r"\b([1-9],?\d{3}|[1-9]\d{2})\b")  # 100+ posts in the title
# Govt posts filled only from serving employees (no fresher can apply). "N years experience" in a title is
# NOT enough: big notifications (SSC Selection Post, KPSC...) mix experience posts with fresher posts, and
# the writer covers the fresher ones after reading the notification.
EXPERIENCE_ONLY = re.compile(r"\b(deputation|absorption|lateral entry|re-?employment|retired|ex-?employees?)\b", re.I)
DIRECT_RECRUIT = re.compile(r"\b(direct recruitment|freshers?|graduates?|apprentices?|trainees?)\b", re.I)


def prefilter(job: dict) -> str | None:
    """Reason to reject without asking the AI, or None."""
    title = job.get("title", "")
    kind = job.get("source_type")
    if NOT_A_VACANCY.search(title):
        return "pre-filter: not a vacancy notice"
    if SENIOR.search(title) and not ENTRY_HINT.search(title) and not BIG_COUNT.search(title):
        return "pre-filter: senior post"
    if kind in ("ats", "email"):  # private jobs must be IT/EC and Bengaluru/remote-from-anywhere
        if HR_ROLE.search(title):  # HR freshers: Bengaluru or remote only (no other cities)
            if not BENGALURU_OR_REMOTE.search(title + " " + job.get("summary", "")):
                return "pre-filter: HR role not in Bengaluru / remote"
        elif NON_IT.search(title):
            return "pre-filter: non-IT private role"
        if FOREIGN_ONLY.search(title + " " + job.get("summary", "")) and not re.search(r"india|bengaluru|bangalore", title + job.get("summary", ""), re.I):
            return "pre-filter: onsite abroad / remote limited to another country"
    elif OTHER_STATE.search(title) and not CENTRAL.search(title):
        return "pre-filter: other state's government post"
    elif EXPERIENCE_ONLY.search(title) and not DIRECT_RECRUIT.search(title):
        return "pre-filter: govt post needs prior experience (deputation/experienced only)"
    return None


def filter_candidates() -> int:
    expire_stale()
    pre_rejected = 0
    with FileLock("jobs", max_age=120, wait=60):
        jobs = load_jobs()
        for j in jobs.values():
            if j.get("status") == "candidate" and j.get("source_type") != "manual":
                why = prefilter(j)
                if why:
                    j.update(status="rejected", reason=why, updated_at=now_iso())
                    pre_rejected += 1
        save_jobs(jobs)
    if pre_rejected:
        log(f"filter: {pre_rejected} rejected by free pre-filter")
    jobs = load_jobs()
    cands = [j for j in jobs.values() if j.get("status") == "candidate"]
    if not cands:
        log("filter: no candidates")
        return 0
    cands = sorted(cands, key=lambda j: j.get("found_at", ""), reverse=True)[: CONFIG["max_candidates_per_filter"]]

    # Python already removes near-identical titles; this short list lets the AI catch reworded repeats.
    recent_titles = [j["title"] for j in jobs.values()
                     if j.get("status") in ("lead_pending", "queued", "writing", "draft_ready", "published")][-30:]
    # Compact payload: one short line per candidate keeps input tokens low.
    lines = []
    for j in cands:
        note = (j.get("summary") or "")[:140].replace("\n", " ")
        lines.append(f'{j["id"]} | {j["title"][:160]} | src: {j["source"].split(" (")[0]}'
                     + (f" | note: {note}" if note else ""))
    covered = recent_titles + existing_blog_slugs()[-30:]
    prompt = ("ALREADY COVERED:\n" + "\n".join(f"- {t}" for t in covered)
              + "\n\nCANDIDATES (id | title | src | note):\n" + "\n".join(lines))
    system = (AUTO_DIR / "prompts" / "filter.md").read_text(encoding="utf-8")

    reply = run_claude(prompt, CONFIG["filter_model"], tools=[], system_prompt=system, effort=CONFIG.get("filter_effort"),
                       timeout=CONFIG["filter_timeout_sec"], budget_usd=CONFIG.get("filter_budget_usd"),
                       cache=False)
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
