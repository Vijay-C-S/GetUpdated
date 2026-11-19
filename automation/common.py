"""Shared helpers: paths, config, state, locking, logging, git, small utils."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

AUTO_DIR = Path(__file__).resolve().parent          # .../GetUpdated/automation
REPO_DIR = AUTO_DIR.parent                           # .../GetUpdated
STATE_DIR = AUTO_DIR / "state"
DRAFTS_DIR = STATE_DIR / "drafts"
JOBS_FILE = STATE_DIR / "jobs.json"
SOURCES_STATE_FILE = STATE_DIR / "sources_state.json"
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_CONFIG = {
    "site_url": "https://www.getupdated.in",
    "git_branch": "main",
    "max_posts_per_day": 3,
    "require_approval": True,
    "min_score": 7,
    "max_candidates_per_filter": 25,
    "claude_bin": "claude",
    "filter_model": "haiku",
    "writer_model": "sonnet",
    "claude_extra_args": [],
    "writer_timeout_sec": 1800,
    "filter_timeout_sec": 300,
    "live_check_timeout_sec": 900,
    "homepage_cards": 3,
    "keywords_include": [
        "hiring", "recruitment", "fresher", "freshers", "intern", "internship",
        "vacancy", "vacancies", "off campus", "off-campus", "graduate", "trainee",
        "apply online", "notification", "walk-in", "batch", "passout", "passouts",
        "engineer", "developer", "analyst", "job", "jobs", "careers", "apprentice",
        "apprenticeship", "trainee", "recruitment",
    ],
    "keywords_exclude": [
        "result", "answer key", "admit card", "layoff", "layoffs", "fired",
        "salary hike", "stock", "shares", "staff nurse", "nursing", "medical officer",
        "doctor", "professor", "faculty", "senior resident", "junior resident",
    ],
}


# --------------------------------------------------------------------------- env/config
def load_env() -> None:
    """Load automation/.env (KEY=VALUE lines) into os.environ without overriding."""
    env_file = AUTO_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg_file = AUTO_DIR / "config.json"
    if cfg_file.exists():
        cfg.update(json.loads(cfg_file.read_text(encoding="utf-8")))
    return cfg


load_env()
CONFIG = load_config()


# --------------------------------------------------------------------------- logging
def log(msg: str) -> None:
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "automation.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- json state
def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class FileLock:
    """Simple cross-process lock using an exclusive lock file (stale after max_age)."""

    def __init__(self, name: str, max_age: int = 3 * 3600, wait: float = 0):
        self.path = STATE_DIR / f"{name}.lock"
        self.max_age = max_age
        self.wait = wait
        self.held = False

    def acquire(self) -> bool:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return True
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.max_age:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() >= deadline:
                    return False
                time.sleep(2)

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"lock '{self.path.name}' is busy")
        return self

    def __exit__(self, *exc):
        self.release()


# --------------------------------------------------------------------------- jobs store
# Job statuses: candidate -> rejected | queued -> writing -> draft_ready -> publishing
#               -> published ;  draft_ready -> skipped ; any -> failed
def load_jobs() -> dict:
    return read_json(JOBS_FILE, {})


def save_jobs(jobs: dict) -> None:
    write_json(JOBS_FILE, jobs)


def update_job(job_id: str, **fields) -> dict:
    with FileLock("jobs", max_age=120, wait=60):
        jobs = load_jobs()
        job = jobs.setdefault(job_id, {"id": job_id})
        job.update(fields)
        job["updated_at"] = now_iso()
        save_jobs(jobs)
        return job


def get_job(job_id: str) -> dict | None:
    return load_jobs().get(job_id)


# --------------------------------------------------------------------------- utils
def now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def today_ist():
    return datetime.now(IST).date()


def url_id(url: str) -> str:
    norm = re.sub(r"[#?].*$", "", url.strip().lower()).rstrip("/")
    norm = re.sub(r"^https?://(www\.)?", "", norm)
    return hashlib.sha1(norm.encode()).hexdigest()[:12]


def slugify(text: str, max_len: int = 90) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-")


STOPWORDS = {"the", "a", "an", "for", "and", "of", "in", "to", "is", "are", "with",
             "on", "at", "by", "hiring", "2025", "2026", "2027", "apply", "online",
             "job", "jobs", "freshers", "fresher", "recruitment", "html"}


def title_tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2 and t not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def existing_blog_slugs() -> list[str]:
    return [p.stem for p in (REPO_DIR / "blogs").glob("*.html")]


def run(cmd: list[str], cwd: Path = REPO_DIR, check: bool = True, timeout: int = 300) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}):\n{proc.stderr or proc.stdout}")
    return proc.stdout


def git_is_clean() -> bool:
    return run(["git", "status", "--porcelain"]).strip() == ""


def fail_exit(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)
