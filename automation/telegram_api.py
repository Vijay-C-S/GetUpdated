"""Minimal Telegram Bot API client (no third-party bot library needed)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from common import log

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in automation/.env")
    return token


def chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def call(method: str, data: dict | None = None, files: dict | None = None, timeout: int = 60) -> dict:
    url = API.format(token=_token(), method=method)
    payload = {}
    for k, v in (data or {}).items():
        payload[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    resp = requests.post(url, data=payload, files=files, timeout=timeout)
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body["result"]


def send_message(text: str, buttons: list[list[dict]] | None = None, chat: str | None = None) -> dict | None:
    target = chat or chat_id()
    if not target:
        log("Telegram: TELEGRAM_CHAT_ID not set, message not sent")
        return None
    data = {"chat_id": target, "text": text[:4096], "disable_web_page_preview": "true"}
    if buttons:
        data["reply_markup"] = {"inline_keyboard": buttons}
    return call("sendMessage", data)


def send_photo(photo: Path, caption: str = "", buttons: list[list[dict]] | None = None) -> dict | None:
    if not chat_id():
        log("Telegram: TELEGRAM_CHAT_ID not set, photo not sent")
        return None
    data = {"chat_id": chat_id(), "caption": caption[:1024]}
    if buttons:
        data["reply_markup"] = {"inline_keyboard": buttons}
    with open(photo, "rb") as fh:
        return call("sendPhoto", data, files={"photo": (photo.name, fh)})


def send_document(path: Path, caption: str = "") -> dict | None:
    if not chat_id():
        return None
    with open(path, "rb") as fh:
        return call("sendDocument", {"chat_id": chat_id(), "caption": caption[:1024]},
                    files={"document": (path.name, fh)})


def notify(text: str) -> None:
    """Best-effort notification; never raises."""
    try:
        send_message(text)
    except Exception as exc:  # noqa: BLE001
        log(f"Telegram notify failed: {exc}")
