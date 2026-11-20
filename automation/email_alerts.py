"""Read LinkedIn / Naukri / Indeed / foundit / Instahyre job-alert emails from an inbox.

This is the allowed way to use those sites: you subscribe to their job alerts (keywords like
"fresher software engineer Bangalore", "graduate engineer trainee electronics", "remote intern"),
the alerts arrive by email, and this reads them over IMAP. Nothing scrapes the sites themselves.

Setup (once): use a separate Gmail account for alerts, turn on 2-Step Verification, create an
App Password, and put these in automation/.env:
  ALERTS_IMAP_USER=youralerts@gmail.com
  ALERTS_IMAP_PASSWORD=<16-letter app password>
Without them this source is silently skipped.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from email.header import decode_header, make_header

from bs4 import BeautifulSoup

from common import log

SENDERS = ("linkedin.com", "naukri.com", "indeed.com", "indeedemail.com", "foundit.in", "instahyre.com",
           "internshala.com", "wellfound.com", "cutshort.io", "hirist", "glassdoor")
# (pattern that marks a job link, function to turn it into a clean canonical URL)
JOB_LINKS = [
    (re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"), lambda m: f"https://www.linkedin.com/jobs/view/{m.group(1)}"),
    (re.compile(r"naukri\.com/job-listings-[^?\s\"']+"), lambda m: "https://www." + m.group(0)),
    (re.compile(r"indeed\.com/(?:rc/clk|viewjob|pagead/clk)\?[^\s\"']*?jk=([0-9a-f]+)"),
     lambda m: f"https://in.indeed.com/viewjob?jk={m.group(1)}"),
    (re.compile(r"internshala\.com/(?:internship|job)/detail/[^?\s\"']+"), lambda m: "https://" + m.group(0)),
    (re.compile(r"foundit\.in/job/[^?\s\"']+"), lambda m: "https://www." + m.group(0)),
    (re.compile(r"instahyre\.com/job-[^?\s\"']+"), lambda m: "https://www." + m.group(0)),
    (re.compile(r"wellfound\.com/jobs/\d+[^?\s\"']*"), lambda m: "https://" + m.group(0)),
]
NOISE = re.compile(r"^(view job|apply|see all jobs|easy apply|view all|unsubscribe|search jobs|save)\b", re.I)


def _html_of(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            txt = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
            return "".join(f'<a href="{u}">{u}</a><br>' for u in re.findall(r"https?://\S+", txt))
    return ""


def parse_alert(html: str, sender: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        for pat, canon in JOB_LINKS:
            m = pat.search(a["href"])
            if not m:
                continue
            url = canon(m)
            text = a.get_text(" ", strip=True)
            item = found.setdefault(url, {"url": url, "title": "", "summary": ""})
            if text and not NOISE.match(text) and len(text) > len(item["title"]):
                item["title"] = text[:160]
                # company · location usually sits right next to the title in these emails
                box = a.find_parent(["td", "div", "tr"])
                ctx = box.get_text(" · ", strip=True) if box else ""
                item["summary"] = ctx.replace(text, "", 1).strip(" ·")[:200]
            break
    site = sender.split("@")[-1].split(">")[0]
    return [dict(i, title=i["title"] or "Job from alert", summary=f"{site} alert: {i['summary']}")
            for i in found.values()]


def fetch_alerts(src: dict) -> list[dict]:
    user, pwd = os.environ.get("ALERTS_IMAP_USER"), os.environ.get("ALERTS_IMAP_PASSWORD")
    if not user or not pwd:
        return []
    host = src.get("imap_host", "imap.gmail.com")
    items: list[dict] = []
    box = imaplib.IMAP4_SSL(host, timeout=60)
    try:
        box.login(user, pwd.replace(" ", ""))
        box.select(src.get("folder", "INBOX"))
        _, data = box.search(None, "UNSEEN")
        ids = data[0].split()[-60:]  # newest 60 unread alerts per run
        for num in ids:
            _, msg_data = box.fetch(num, "(RFC822)")  # marks it read, so it is processed once
            msg = email.message_from_bytes(msg_data[0][1])
            sender = str(make_header(decode_header(msg.get("From", "")))).lower()
            if not any(s in sender for s in SENDERS):
                continue
            items += parse_alert(_html_of(msg), sender)
        log(f"email_alerts: {len(ids)} unread mail(s), {len(items)} job link(s)")
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
    return items
