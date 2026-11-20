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

import requests
from bs4 import BeautifulSoup

from common import log

SENDERS = ("linkedin.com", "naukri.com", "indeed.com", "indeedemail.com", "foundit.in", "instahyre.com",
           "internshala.com", "wellfound.com", "cutshort.io", "hirist", "glassdoor")
# (pattern that marks a job link, function to turn it into a clean canonical URL)
JOB_LINKS = [
    (re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"), lambda m: f"https://www.linkedin.com/jobs/view/{m.group(1)}"),
    (re.compile(r"naukri\.com/job-listings-[^?\s\"']+"), lambda m: "https://www." + m.group(0)),
    (re.compile(r"indeed\.com/(?:rc/clk(?:/dl)?|viewjob|pagead/clk|m/viewjob)\?[^\s\"']*?jk=([0-9a-f]+)"),
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


FOOTER = re.compile(r"privacy|terms|help|unsubscribe|manage|edit|settings|create your|download|app store|"
                    r"google play|view all|see all|since yesterday|last \d+ days|preferences|feedback", re.I)
TRACKERS = ("engage.indeed.com", "link.internshala.com", "click.", "email.", "links.", "trk.", "/track", "emailclick")


def _resolve(url: str, cache: dict) -> str:
    """Alert emails wrap job links in click-tracking redirects; follow them like a click would."""
    if url not in cache:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, allow_redirects=True, stream=True)
            cache[url] = r.url
            r.close()
        except Exception:  # noqa: BLE001
            cache[url] = url
    return cache[url]


def _canonical(href: str):
    for pat, canon in JOB_LINKS:
        m = pat.search(href)
        if m:
            return canon(m)
    return None


def parse_alert(html: str, sender: str, max_resolve: int = 25) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    cache: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href, text = a["href"], a.get_text(" ", strip=True)
        url = _canonical(href)
        if not url:
            if len(text) < 6 or NOISE.match(text) or FOOTER.search(text):
                continue
            if not any(t in href for t in TRACKERS) or len(cache) >= max_resolve:
                continue
            url = _canonical(_resolve(href, cache))
            if not url:
                continue
        item = found.setdefault(url, {"url": url, "title": "", "summary": ""})
        if text and not NOISE.match(text) and (not item["title"] or len(text) < len(item["title"])):
            # the shortest non-empty anchor is usually the clean job title
            if not item["summary"] and item["title"]:
                item["summary"] = item["title"]
            item["title"] = text[:160]
        if not item["summary"]:
            box = a.find_parent(["td", "div", "tr"])
            item["summary"] = (box.get_text(" · ", strip=True) if box else "")[:200]
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
