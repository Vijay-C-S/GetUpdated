"""Run Claude Code headless (`claude -p`) using the subscription logged in on this machine."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from common import CONFIG, REPO_DIR, log


class UsageLimitError(RuntimeError):
    """Raised when the Claude plan's usage limit is reached; retry later."""


def run_claude(prompt: str, model: str, allowed_tools: list[str] | None = None,
               timeout: int = 600, cwd: Path = REPO_DIR, effort: str | None = None) -> str:
    cmd = [CONFIG["claude_bin"], "-p", "--model", model, "--output-format", "json"]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if effort:
        cmd += ["--effort", effort]
    cmd += list(CONFIG.get("claude_extra_args", []))
    log(f"claude: model={model} effort={effort or 'default'} tools={allowed_tools or 'none'}")
    proc = subprocess.run(cmd, input=prompt, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    out = proc.stdout.strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {"is_error": True, "result": out or proc.stderr}
    result = str(data.get("result", ""))
    if proc.returncode != 0 or data.get("is_error"):
        blob = (result + " " + proc.stderr).lower()
        if "limit" in blob and ("usage" in blob or "reset" in blob or "rate" in blob):
            raise UsageLimitError(result or proc.stderr)
        raise RuntimeError(f"claude failed ({proc.returncode}): {(result or proc.stderr)[:800]}")
    cost = data.get("total_cost_usd")
    log(f"claude: done in {data.get('num_turns', '?')} turns" + (f", ~${cost:.3f} equiv" if cost else ""))
    return result


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply."""
    text = text.strip()
    fence = text.find("```")
    if fence != -1:
        inner = text[fence + 3:]
        inner = inner[inner.find("\n") + 1:] if inner[:4].lower().startswith("json") else inner
        end = inner.find("```")
        if end != -1:
            text = inner[:end]
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in reply")
    start = min(starts)
    closer = "]" if text[start] == "[" else "}"
    end = text.rfind(closer)
    return json.loads(text[start:end + 1])
