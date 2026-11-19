"""Run Claude Code headless (`claude -p`) using the subscription logged in on this machine.

Token-saving defaults:
- a short custom system prompt replaces Claude Code's large default agent prompt
- only the tools a step needs are loaded (`--tools`), no MCP servers, no slash commands
- no session files are written
- static instructions go in the system prompt (identical across jobs -> prompt-cache hits),
  per-job data goes in the user message
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from common import CONFIG, REPO_DIR, log


class UsageLimitError(RuntimeError):
    """Raised when the Claude plan's usage limit is reached; retry later."""


def run_claude(prompt: str, model: str, tools: list[str] | None = None, timeout: int = 600,
               cwd: Path = REPO_DIR, effort: str | None = None, system_prompt: str | None = None,
               budget_usd: float | None = None) -> str:
    cmd = [CONFIG["claude_bin"], "-p", "--model", model, "--output-format", "json",
           "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
           "--tools", ",".join(tools or [])]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    if effort:
        cmd += ["--effort", effort]
    if budget_usd:
        cmd += ["--max-budget-usd", str(budget_usd)]
    cmd += list(CONFIG.get("claude_extra_args", []))
    log(f"claude: model={model} effort={effort or 'default'} tools={tools or 'none'}")
    proc = subprocess.run(cmd, input=prompt, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    out = proc.stdout.strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {"is_error": True, "result": out or proc.stderr}
    result = str(data.get("result", ""))
    usage = data.get("usage") or {}
    log("claude: done in {t} turns, in={i} cache_read={c} out={o}{cost}".format(
        t=data.get("num_turns", "?"), i=usage.get("input_tokens", "?"),
        c=usage.get("cache_read_input_tokens", "?"), o=usage.get("output_tokens", "?"),
        cost=f", ~${data['total_cost_usd']:.3f} equiv" if data.get("total_cost_usd") else ""))
    if proc.returncode != 0 or data.get("is_error"):
        blob = (result + " " + proc.stderr).lower()
        if "limit" in blob and ("usage" in blob or "reset" in blob or "rate" in blob) and "budget" not in blob:
            raise UsageLimitError(result or proc.stderr)
        raise RuntimeError(f"claude failed ({proc.returncode}): {(result or proc.stderr)[:800]}")
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
