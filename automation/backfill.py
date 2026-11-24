"""Backfill: find central + Karnataka govt recruitments that are still open but we never covered.

Sonnet searches the web (official notifications first), Python drops anything closed, closing too
soon, or already covered, and each survivor becomes a normal lead on Telegram (✍️ Write draft / ❌ Ignore).
The writer still verifies every fact on the official notification before a draft is made.

Usage:
  python backfill.py            # run now (cron: once a day; Telegram: /backfill)
"""
from __future__ import annotations

from datetime import date, timedelta

import telegram_api as tg
from claude_cli import UsageLimitError, extract_json, run_claude
from common import (AUTO_DIR, CONFIG, FileLock, existing_blog_slugs, load_jobs, log, now_iso, save_jobs,
                    today_ist, url_id)
from watchdog import is_duplicate

SOURCE = "Backfill (past govt recruitments still open)"
CATEGORIES = {"Govt Jobs", "Karnataka Govt Jobs", "Bank Jobs", "Apprenticeship"}


def covered_titles(jobs: dict) -> list[str]:
    """Everything we already have or already refused, so the model doesn't suggest it again."""
    titles = [j.get("title", "") for j in sorted(jobs.values(), key=lambda j: j.get("found_at", ""))
              if j.get("status") in ("lead_pending", "queued", "writing", "draft_ready", "publishing",
                                     "published", "skipped", "failed")
              or j.get("source") == SOURCE]
    return [t for t in titles if t][-150:] + existing_blog_slugs()[-150:]


def backfill() -> int:
    lock = FileLock("backfill", max_age=3 * 3600)
    if not lock.acquire():
        log("backfill: already running; exiting")
        return 0
    try:
        min_days = int(CONFIG.get("backfill_min_days_left", 3))
        earliest = today_ist() + timedelta(days=min_days)
        jobs = load_jobs()
        prompt = (f"TODAY: {today_ist().isoformat()} (IST)\n"
                  f"EARLIEST LAST DATE: {earliest.isoformat()} (skip anything closing before this)\n\n"
                  "ALREADY COVERED:\n" + "\n".join(f"- {t}" for t in covered_titles(jobs)))
        system = (AUTO_DIR / "prompts" / "backfill.md").read_text(encoding="utf-8")
        reply = run_claude(prompt, CONFIG.get("backfill_model", "sonnet"), tools=["WebSearch", "WebFetch"],
                           system_prompt=system, effort=CONFIG.get("backfill_effort", "low"),
                           timeout=int(CONFIG.get("backfill_timeout_sec", 1800)),
                           budget_usd=CONFIG.get("backfill_budget_usd", 1.5))  # cached: ~37 turns re-read the same context
        found = [x for x in extract_json(reply) if isinstance(x, dict)]

        new_ids, skipped = [], []
        with FileLock("jobs", max_age=120, wait=60):
            jobs = load_jobs()
            slugs = existing_blog_slugs()
            for it in found:
                title, url = (it.get("title") or "").strip(), (it.get("official_url") or "").strip()
                if not title or not url.startswith("http"):
                    continue
                try:
                    last = date.fromisoformat(str(it.get("last_date", ""))[:10])
                except ValueError:
                    skipped.append(f"{title}: no last date")
                    continue
                if last < earliest:
                    skipped.append(f"{title}: closes {last}")
                    continue
                if int(it.get("score", 0) or 0) < CONFIG["min_score"]:
                    continue
                jid = url_id(url)
                dup = ("already have this link" if jid in jobs
                       else is_duplicate(f"{it.get('company', '')} {it.get('role', '')} {title}", jobs, slugs)
                       or is_duplicate(title, jobs, slugs))
                if dup:
                    skipped.append(f"{title}: {dup}")
                    continue
                cat = it.get("category") if it.get("category") in CATEGORIES else "Govt Jobs"
                summary = (f"Last date: {last.isoformat()} · Eligibility: {it.get('eligibility', '')}"
                           + ("" if it.get("confirmed_official") else " · (last date not yet confirmed on the official page)"))
                jobs[jid] = {
                    "id": jid, "title": title[:160], "url": url, "summary": summary[:300],
                    "source": SOURCE, "source_type": "backfill", "official": bool(it.get("confirmed_official")),
                    "status": "lead_pending" if CONFIG.get("require_lead_approval", True) else "queued",
                    "score": int(it.get("score", 8)), "company": it.get("company", ""), "role": it.get("role", ""),
                    "category": cat, "reason": f"Still open till {last.strftime('%d %b %Y')} · {it.get('reason', '')}",
                    "last_date": last.isoformat(), "found_at": now_iso(), "updated_at": now_iso(),
                    "lead_notified": False,
                }
                new_ids.append(jid)
            save_jobs(jobs)

        log(f"backfill: {len(found)} found, {len(new_ids)} new lead(s), {len(skipped)} skipped"
            + (f" ({'; '.join(skipped)[:500]})" if skipped else ""))
        if new_ids:
            from pipeline import send_lead
            tg.send_message(f"🔎 Backfill: {len(new_ids)} govt recruitment(s) still open that we haven't covered.")
            jobs = load_jobs()
            for jid in sorted(new_ids, key=lambda j: jobs[j]["last_date"]):  # closing soonest first
                send_lead(jobs[jid])
        return len(new_ids)
    except UsageLimitError:
        tg.notify("⏸ Claude usage limit reached during backfill; will try again tomorrow.")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"backfill: failed: {exc}")
        tg.notify(f"⚠️ Backfill failed: {str(exc)[:300]}")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    n = backfill()
    print(f"{n} new lead(s)")
