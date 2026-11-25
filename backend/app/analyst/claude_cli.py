"""Answer with Claude through the Claude Code CLI (`claude -p`) on this computer, instead of the local Ollama model.

Unlike Ollama, this sends the case facts and the question to Anthropic. It is off unless ANALYST_PROVIDER=claude_cli
or the analyst picks "Claude CLI" in the AI Analyst tab.

Safety: the CLI runs with no tools at all (it cannot read or change files or run commands), at most one turn,
without project settings, MCP servers or slash commands, without saving the session, from a new empty temporary
folder. Everything it reads (rules, fact sheet, conversation) goes in through stdin, so nothing long or quoted is
put on the command line (Windows runs `claude.cmd` through cmd.exe and limits the command line length).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import time

from app.analyst.ollama import AnalystUnavailable
from app.analyst.settings import AnalystSettings

# Short, plain ASCII: cmd.exe sees this. The real rules travel in stdin.
_SYSTEM_PROMPT = "You are the AI analyst in a blockchain intelligence dashboard. Follow the RULES in the message exactly."
NOT_INSTALLED = "The Claude CLI is not installed here (no `{command}` on PATH). Install Claude Code, or switch to the local model."
NOT_SIGNED_IN = "The Claude CLI is not signed in. In a terminal run `claude`, then type /login, and try again."


def _command(settings: AnalystSettings) -> str | None:
    return shutil.which(settings.claude_command)


def _run(args: list[str], stdin: str, timeout: float, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the CLI and wait (a plain function so tests can replace it with a fake)."""
    return subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=cwd)


def _flatten(messages: list[dict]) -> str:
    """One stdin text from chat messages: the rules, the case facts, then the conversation, newest question last."""
    parts = []
    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            parts.append("RULES\n" + content)
        elif role == "user" and content.startswith("CASE FACTS"):
            parts.append(content)
        else:
            parts.append(("ANALYST (you, earlier)" if role == "assistant" else "INVESTIGATOR") + ":\n" + content)
    parts.append("Answer the investigator's last message now, following the RULES. Plain text or light Markdown.")
    return "\n\n".join(parts)


def _is_auth_error(text: str) -> bool:
    text = text.lower()
    return any(k in text for k in ("failed to authenticate", "not logged in", "please run /login", "oauth",
                                   "invalid api key", "authentication"))


_status_cache: dict[str, tuple[float, dict]] = {}
STATUS_CACHE_SECONDS = 60


async def status(settings: AnalystSettings) -> dict:
    """Is the CLI installed and signed in? Uses `claude --version` and `claude auth status`: no tokens are spent.

    The answer is kept for a minute, so opening the tab does not start two CLI processes every time.
    """
    cached = _status_cache.get(settings.claude_command)
    if cached and time.monotonic() - cached[0] < STATUS_CACHE_SECONDS:
        return {**cached[1], "model": settings.claude_model}
    info = await _status(settings)
    _status_cache[settings.claude_command] = (time.monotonic(), info)
    return info


async def _status(settings: AnalystSettings) -> dict:
    info = {"model": settings.claude_model, "installed": False, "signed_in": False, "ready": False, "version": None}
    command = _command(settings)
    if not command:
        info["detail"] = NOT_INSTALLED.format(command=settings.claude_command)
        return info
    info["installed"] = True

    def check() -> tuple[str | None, bool | None]:
        version = signed_in = None
        try:
            out = _run([command, "--version"], "", 20)
            version = out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else None
            out = _run([command, "auth", "status"], "", 20)
            signed_in = bool(json.loads(out.stdout).get("loggedIn")) if out.stdout.strip() else False
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return version, signed_in

    version, signed_in = await asyncio.to_thread(check)
    info["version"] = version
    info["signed_in"] = bool(signed_in)
    info["ready"] = info["signed_in"]
    info["detail"] = "ready" if info["ready"] else NOT_SIGNED_IN
    return info


async def chat(settings: AnalystSettings, messages: list[dict]) -> dict:
    """One answer from Claude. Returns {"text", "model", "output_tokens", "prompt_tokens", "tokens_per_second"}."""
    command = _command(settings)
    if not command:
        raise AnalystUnavailable(NOT_INSTALLED.format(command=settings.claude_command))
    _status_cache.pop(settings.claude_command, None)  # an answer (or its failure) is fresher news than the cache
    args = [command, "-p", "--output-format", "json", "--model", settings.claude_model,
            "--tools", "", "--max-turns", "1", "--no-session-persistence", "--strict-mcp-config",
            "--disable-slash-commands", "--setting-sources", "", "--system-prompt", _SYSTEM_PROMPT]
    stdin = _flatten(messages)

    def call() -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="analyst-claude-") as empty:
            return _run(args, stdin, settings.claude_timeout_seconds, cwd=empty)

    started = time.perf_counter()
    try:
        out = await asyncio.to_thread(call)
    except subprocess.TimeoutExpired as exc:
        raise AnalystUnavailable(
            f"Claude did not answer within {settings.claude_timeout_seconds:.0f} seconds.") from exc
    except OSError as exc:
        raise AnalystUnavailable(f"Could not start the Claude CLI: {exc}") from exc

    try:
        data = json.loads(out.stdout)
    except ValueError:
        text = (out.stderr or out.stdout or "").strip()
        if _is_auth_error(text):
            raise AnalystUnavailable(NOT_SIGNED_IN)
        raise AnalystUnavailable(f"The Claude CLI failed (exit {out.returncode}): {text[:200] or 'no output'}")
    if data.get("is_error") or out.returncode != 0:
        text = str(data.get("result") or out.stderr or "")
        if _is_auth_error(text):
            raise AnalystUnavailable(NOT_SIGNED_IN)
        raise AnalystUnavailable(f"The Claude CLI returned an error: {text[:200] or 'unknown error'}")

    usage = data.get("usage") or {}
    output_tokens = usage.get("output_tokens")
    prompt_tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens")) or None
    api_seconds = (data.get("duration_api_ms") or 0) / 1000
    models = list((data.get("modelUsage") or {}).keys())
    return {
        "text": str(data.get("result") or ""),
        "model": models[0] if models else settings.claude_model,
        "output_tokens": output_tokens,
        "prompt_tokens": prompt_tokens,
        "tokens_per_second": round(output_tokens / api_seconds, 1) if output_tokens and api_seconds else None,
        "wall_seconds": round(time.perf_counter() - started, 1),
    }
