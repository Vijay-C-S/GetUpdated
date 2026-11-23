"""Pipeline entry point (run by cron): watch -> filter -> write drafts -> ask for approval.

Usage:
  python pipeline.py              # full run
  python pipeline.py --watch-only # just scan sources
  python pipeline.py --write ID   # write one specific job now (used by the bot's /post)
"""
from __future__ import annotations

import argparse
import json

import telegram_api as tg
from claude_cli import UsageLimitError
from common import CONFIG, DRAFTS_DIR, FileLock, get_job, load_jobs, log, run, today_ist, update_job
from filter_jobs import filter_candidates
from publisher import publish
from watchdog import scan
from writer import NeedsOwnerConfirm, write_job


def posts_today() -> int:
    today = today_ist().isoformat()
    return sum(1 for j in load_jobs().values()
               if j.get("drafted_on") == today and j.get("status") in ("draft_ready", "publishing", "published"))


def send_for_approval(job_id: str) -> None:
    job = get_job(job_id)
    draft = DRAFTS_DIR / job_id
    meta = json.loads((draft / "meta.json").read_text(encoding="utf-8"))
    facts = "\n".join(f"• {f}" for f in meta.get("key_facts", [])[:8])
    caption = (f"📝 NEW DRAFT ({job.get('score', '-')}/10)\n{meta['title']}\n\n{facts}\n\n"
               f"🔗 Official: {meta['official_url']}\n"
               f"🔎 Check: {meta.get('verification_notes', '')}")
    if CONFIG["require_approval"]:
        buttons = [[{"text": "✅ Publish", "callback_data": f"pub:{job_id}"},
                    {"text": "❌ Skip", "callback_data": f"skip:{job_id}"}]]
        tg.send_photo(draft / "banner.webp", caption=caption[:1024], buttons=buttons)
        tg.send_document(draft / "post.html", caption="Full draft (open in browser to read)")
    else:
        publish(job_id)


def write_and_dispatch(job_id: str) -> None:
    try:
        write_job(job_id)
        send_for_approval(job_id)
    except NeedsOwnerConfirm as exc:
        update_job(job_id, status="confirm_pending", error=str(exc)[:500])
        ask_owner(job_id)
    except UsageLimitError as exc:
        update_job(job_id, status="queued")
        tg.notify(f"⏸ Claude usage limit reached; will retry next run.\n{str(exc)[:300]}")
        raise
    except Exception as exc:  # noqa: BLE001
        update_job(job_id, status="failed", error=str(exc)[:1000])
        job = get_job(job_id) or {}
        tg.notify(f"⚠️ Skipped: {job.get('title', job_id)}\nReason: {str(exc)[:500]}")
        log(f"pipeline: job {job_id} failed: {exc}")


def ask_owner(job_id: str) -> None:
    """No official source found: you check whether the job is real, the writer then uses the job-site text."""
    job = get_job(job_id) or {}
    text = (f"❓ CAN'T CONFIRM: {job.get('title', job_id)}\n\n"
            f"Why: {job.get('error') or 'no official link found'}\n"
            f"🔗 {job.get('url') or '(no link)'}\n\n"
            "Does this job really exist and is it open? ✅ = write it from the job-site text.")
    buttons = [[{"text": "✅ Exists, write it", "callback_data": f"ok:{job_id}"},
                {"text": "❌ No / fake", "callback_data": f"no:{job_id}"}]]
    try:
        tg.send_message(text, buttons=buttons)
    except Exception as exc:  # noqa: BLE001
        log(f"pipeline: could not ask owner about {job_id}: {exc}")


def git_pull() -> bool:
    """Get posts you pushed manually, so templates and dedupe are current."""
    try:
        with FileLock("git", wait=600):
            run(["git", "pull", "--rebase", "origin", CONFIG["git_branch"]], timeout=120)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"pipeline: git pull failed: {exc}")
        tg.notify(f"⚠️ git pull failed on the VM: {str(exc)[:300]}")
        return False


def send_lead(job: dict) -> None:
    """Telegram: a new lead passed the filter. You decide whether to spend a draft on it."""
    text = (f"🆕 NEW LEAD ({job.get('score', '-')}/10) · {job.get('category') or job.get('source', '')}\n"
            + (f"{job.get('company', '')} - {job.get('role', '')}\n" if job.get("company") else "")
            + f"\n{job.get('title', '')}\n\n"
            f"💡 {job.get('reason', '')}\n"
            f"🔗 {job.get('url') or '(no link - the writer will search for the official notice)'}")
    buttons = [[{"text": "✍️ Write draft", "callback_data": f"draft:{job['id']}"},
                {"text": "❌ Ignore", "callback_data": f"ign:{job['id']}"}]]
    try:
        tg.send_message(text, buttons=buttons)
        update_job(job["id"], lead_notified=True)
    except Exception as exc:  # noqa: BLE001
        log(f"pipeline: could not send lead {job['id']}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch-only", action="store_true")
    ap.add_argument("--write", metavar="JOB_ID")
    args = ap.parse_args()

    # Approved drafts (--write) wait their turn; scheduled runs just skip if busy.
    lock = FileLock("pipeline", wait=3 * 3600 if args.write else 0)
    if not lock.acquire():
        log("pipeline: another run is in progress; exiting")
        return
    try:
        if args.write:
            git_pull()
            write_and_dispatch(args.write)
            return
        scan()
        if args.watch_only:
            return
        try:
            filter_candidates()
        except UsageLimitError:
            tg.notify("⏸ Claude usage limit reached during filtering; will retry next run.")
            return
        except Exception as exc:  # noqa: BLE001
            log(f"pipeline: filter failed: {exc}")
            tg.notify(f"⚠️ Filter step failed: {str(exc)[:300]}")

        # Ask you about each new good lead (✍️ Write draft / ❌ Ignore).
        for job in sorted((j for j in load_jobs().values()
                           if j.get("status") == "lead_pending" and not j.get("lead_notified")),
                          key=lambda j: -int(j.get("score", 0))):
            send_lead(job)

        if CONFIG.get("require_lead_approval", True):
            return  # drafts start only when you tap ✍️ (bot runs pipeline.py --write <id>)

        if not git_pull():
            return
        cap = int(CONFIG.get("max_posts_per_day") or 0)
        queued = sorted((j for j in load_jobs().values() if j.get("status") == "queued"),
                        key=lambda j: (-int(j.get("priority", 0)), -int(j.get("score", 0))))
        if cap:
            queued = queued[:max(cap - posts_today(), 0)]
        log(f"pipeline: writing {len(queued)} queued job(s)")
        for job in queued:
            try:
                write_and_dispatch(job["id"])
            except UsageLimitError:
                break
    finally:
        lock.release()


if __name__ == "__main__":
    main()
