"""Telegram bot (runs 24/7 as a systemd service).

Buttons:   ✅ Publish / ❌ Skip on each draft
Commands:  /start  /help  /status  /pending  /run  /backfill  /post <url> [note]  /index <url>
Forwarded / pasted job posts become leads.
Only messages from TELEGRAM_CHAT_ID are obeyed.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time

import telegram_api as tg
from common import AUTO_DIR, load_jobs, log, now_iso, today_ist, update_job, url_id
from publisher import publish, seo_ping

HELP = (
    "GetUpdated automation bot\n\n"
    "/status  - today's numbers and queue\n"
    "/pending - re-send leads and drafts waiting for you\n"
    "/run     - check sources for new leads now\n"
    "/post <url> [note] - write a blog for this job link right away\n"
    "/index <url> - tell Google + Bing about a post you published by hand\n"
    "/help    - this message\n\n"
    "Forward or paste any job post (WhatsApp channel, LinkedIn, Telegram...) and I'll offer to draft it."
)


def run_pipeline_bg(*args: str) -> None:
    subprocess.Popen([sys.executable, str(AUTO_DIR / "pipeline.py"), *args], cwd=str(AUTO_DIR),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def do_publish(job_id: str) -> None:
    try:
        tg.send_message(f"⏳ Publishing {job_id}... (push + waiting for GitHub Pages)")
        publish(job_id)
    except Exception as exc:  # noqa: BLE001
        update_job(job_id, status="failed", error=str(exc)[:1000])
        tg.notify(f"❌ Publish failed for {job_id}: {str(exc)[:600]}")
        log(f"bot: publish {job_id} failed: {exc}")


def handle_callback(cb: dict) -> None:
    data = cb.get("data", "")
    action, _, job_id = data.partition(":")
    tg.call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Got it"})
    msg = cb.get("message", {})
    try:  # remove the buttons so they can't be pressed twice
        tg.call("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                           "reply_markup": {"inline_keyboard": []}})
    except Exception:  # noqa: BLE001
        pass
    job = load_jobs().get(job_id)
    if not job:
        tg.send_message(f"Unknown job {job_id}")
        return
    if action == "draft":
        if job.get("status") not in ("lead_pending", "failed", "rejected"):
            tg.send_message(f"Lead is '{job.get('status')}' already.")
            return
        update_job(job_id, status="queued", priority=5)
        tg.send_message(f"✍️ Writing draft: {job.get('company') or ''} - {job.get('role') or job.get('title')}\n"
                        "I'll send it here for Publish / Skip in a few minutes.")
        run_pipeline_bg("--write", job_id)
    elif action == "ok":  # you confirmed a job the writer couldn't confirm officially
        if job.get("status") != "confirm_pending":
            tg.send_message(f"Job is '{job.get('status')}' already.")
            return
        update_job(job_id, status="queued", owner_confirmed=True, priority=5)
        tg.send_message(f"✍️ Confirmed. Writing from the job-site text: {job.get('title')}")
        run_pipeline_bg("--write", job_id)
    elif action == "no":
        update_job(job_id, status="rejected", reason="you said it doesn't exist")
        tg.send_message(f"🗑 Dropped: {job.get('title')}")
    elif action == "ign":
        update_job(job_id, status="rejected", reason="ignored by you")
        tg.send_message(f"🙈 Ignored: {job.get('company') or ''} - {job.get('role') or job.get('title')}")
    elif action == "pub":
        if job.get("status") != "draft_ready":
            tg.send_message(f"Job is '{job.get('status')}', not waiting for approval.")
            return
        threading.Thread(target=do_publish, args=(job_id,), daemon=True).start()
    elif action == "skip":
        update_job(job_id, status="skipped")
        tg.send_message(f"🗑 Skipped: {job.get('draft_title') or job.get('title')}")


def cmd_status() -> str:
    jobs = load_jobs().values()
    today = today_ist().isoformat()
    count = lambda s: sum(1 for j in jobs if j.get("status") == s)  # noqa: E731
    pub_today = sum(1 for j in jobs if j.get("published_on") == today)
    return (f"📊 Status ({today})\n"
            f"Published today: {pub_today}\n"
            f"Leads waiting for you: {count('lead_pending')}\n"
            f"Drafts waiting approval: {count('draft_ready')}\n"
            f"Writing now: {count('writing')}\n"
            f"Queued to write: {count('queued')}\n"
            f"New candidates: {count('candidate')}\n"
            f"Failed: {count('failed')}  Rejected: {count('rejected')}")


def cmd_pending() -> None:
    from pipeline import ask_owner, send_for_approval, send_lead
    jobs = load_jobs().values()
    leads = [j for j in jobs if j.get("status") == "lead_pending"]
    drafts = [j for j in jobs if j.get("status") == "draft_ready"]
    confirms = [j for j in jobs if j.get("status") == "confirm_pending"]
    if not leads and not drafts and not confirms:
        tg.send_message("Nothing waiting for you.")
    for j in sorted(leads, key=lambda j: -int(j.get("score", 0))):
        send_lead(j)
    for j in drafts:
        send_for_approval(j["id"])
    for j in confirms:
        ask_owner(j["id"])


def cmd_post(arg: str) -> None:
    parts = arg.split(maxsplit=1)
    if not parts or not parts[0].startswith("http"):
        tg.send_message("Usage: /post <job url> [optional note]")
        return
    url, note = parts[0], (parts[1] if len(parts) > 1 else "")
    jid = url_id(url)
    update_job(jid, title=note or url, url=url, summary="", source="manual (Telegram)",
               manual_note=note, status="queued", priority=10, score=10, found_at=now_iso())
    tg.send_message(f"✍️ Writing a blog for:\n{url}\nI'll send the draft here in a few minutes.")
    run_pipeline_bg("--write", jid)


URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
NOT_JOB_LINK = ("whatsapp.com", "wa.me", "t.me/", "telegram.", "instagram.", "youtube.", "youtu.be",
                "facebook.", "chat.whatsapp")


def handle_forward(msg: dict, text: str) -> None:
    """A job post you forwarded or pasted (WhatsApp channel, Telegram, LinkedIn, anywhere).
    Becomes a lead with ✍️ / ❌ buttons. No AI tokens are used until you tap ✍️."""
    from pipeline import send_lead
    from watchdog import is_duplicate
    from common import existing_blog_slugs
    urls = URL_RE.findall(text)
    for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
        if ent.get("type") == "text_link":
            urls.append(ent["url"])
    urls = [u.rstrip(".,;") for u in urls if not any(b in u.lower() for b in NOT_JOB_LINK)]
    first_line = next((l.strip(" *_-•📢🔥✅") for l in text.splitlines() if len(l.strip()) > 8), text[:120])
    url = urls[0] if urls else ""
    jid = url_id(url or text[:300])
    jobs = load_jobs()
    if jid in jobs and jobs[jid].get("status") not in ("rejected", "failed", "skipped"):
        tg.send_message(f"Already have this one (status: {jobs[jid].get('status')}).")
        return
    dup = is_duplicate(first_line, jobs, existing_blog_slugs())
    update_job(jid, title=first_line[:160], url=url, summary=text[:300], source="manual (forwarded by you)",
               source_type="manual", forwarded_text=text[:3000], status="lead_pending", score=8,
               category="", reason=f"Forwarded by you{' - looks ' + dup if dup else ''}",
               found_at=now_iso(), lead_notified=False)
    send_lead(load_jobs()[jid])


def handle_message(msg: dict) -> None:
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text.startswith("/"):
        if len(text) > 20:
            handle_forward(msg, text)
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd in ("/start", "/help"):
        tg.send_message(HELP)
    elif cmd == "/status":
        tg.send_message(cmd_status())
    elif cmd == "/pending":
        cmd_pending()
    elif cmd == "/run":
        tg.send_message("🔍 Running watchdog + writer now...")
        run_pipeline_bg()
    elif cmd == "/backfill":
        tg.send_message("🔎 Searching older central + Karnataka govt recruitments that are still open "
                        "(takes 5-15 min)...")
        subprocess.Popen([sys.executable, str(AUTO_DIR / "backfill.py")], cwd=str(AUTO_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif cmd == "/post":
        cmd_post(arg.strip())
    elif cmd == "/index":
        urls = [u for u in arg.split() if u.startswith("https://www.getupdated.in/")]
        if not urls:
            tg.send_message("Usage: /index <getupdated.in post url> [more urls]\n"
                            "Use it after you publish a post by hand.")
        for u in urls[:10]:
            tg.send_message(f"🔎 {u}\n{seo_ping(u)}")


def main() -> None:
    owner = tg.chat_id()
    log(f"bot: started (owner chat {owner or 'NOT SET'})")
    offset = 0
    while True:
        try:
            updates = tg.call("getUpdates", {"offset": offset, "timeout": 50}, timeout=70)
        except Exception as exc:  # noqa: BLE001
            log(f"bot: getUpdates error: {exc}")
            time.sleep(10)
            continue
        for up in updates:
            offset = up["update_id"] + 1
            try:
                if "callback_query" in up:
                    cb = up["callback_query"]
                    if str(cb["from"]["id"]) == owner or str(cb.get("message", {}).get("chat", {}).get("id")) == owner:
                        handle_callback(cb)
                elif "message" in up:
                    msg = up["message"]
                    chat = str(msg["chat"]["id"])
                    if not owner:
                        # setup helper: tell the user their chat id
                        log(f"bot: message from chat {chat} (@{msg.get('from', {}).get('username', '?')}) "
                            "while TELEGRAM_CHAT_ID is not set")
                        tg.send_message(f"Your chat id is {chat}. Put TELEGRAM_CHAT_ID={chat} in automation/.env "
                                        "and restart the bot.", chat=chat)
                    elif chat == owner:
                        handle_message(msg)
            except Exception as exc:  # noqa: BLE001
                log(f"bot: error handling update: {exc}")
                tg.notify(f"Bot error: {str(exc)[:300]}")


if __name__ == "__main__":
    main()
