"""AI analyst settings, read from environment variables (backend/.env is loaded by app.config)."""
import os
from dataclasses import dataclass

import app.config  # noqa: F401  (loads backend/.env before the variables below are read)


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


@dataclass(frozen=True)
class AnalystSettings:
    enabled: bool
    # Ollama's local API. Keep it on localhost: it has no sign-in of its own.
    ollama_url: str
    # Any Ollama model with tool calling, e.g. qwen3:8b. Pull it first: ollama pull qwen3:8b
    model: str
    # Context window in tokens. 10240 keeps qwen3:8b (4-bit) almost all on an 8 GB GPU; real case prompts
    # were about 5,100 to 5,300 tokens in the 2026-11-24 test.
    num_ctx: int
    # Let models that can "think" reason before answering: slower, usually more careful.
    think: bool
    timeout_seconds: float
    # How many rounds of tool calls one chat answer may use before it must answer.
    max_tool_rounds: int
    # Longest answer, in tokens (Ollama num_predict). Keeps facts + a lookup + the answer inside num_ctx.
    max_answer_tokens: int = 800
    # Who answers by default: "ollama" (local, nothing leaves this computer) or "claude_cli" (Claude through the
    # Claude Code CLI on this computer; the case facts go to Anthropic). The AI Analyst tab can pick per question.
    provider: str = "ollama"
    # The Claude CLI command, the Claude model it uses, and how long one answer may take.
    claude_command: str = "claude"
    claude_model: str = "sonnet"
    claude_timeout_seconds: float = 300.0


def analyst_settings() -> AnalystSettings:
    return AnalystSettings(
        enabled=_flag("ANALYST_ENABLED", "true"),
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434").strip().rstrip("/"),
        model=os.getenv("ANALYST_MODEL", "qwen3:8b").strip(),
        num_ctx=_int("ANALYST_NUM_CTX", 10240, 2048, 131072),
        think=_flag("ANALYST_THINK", "false"),
        timeout_seconds=float(os.getenv("ANALYST_TIMEOUT_SECONDS", "180")),
        max_tool_rounds=_int("ANALYST_MAX_TOOL_ROUNDS", 3, 0, 6),
        max_answer_tokens=_int("ANALYST_MAX_ANSWER_TOKENS", 800, 200, 4000),
        provider=_provider(os.getenv("ANALYST_PROVIDER", "ollama")),
        claude_command=os.getenv("ANALYST_CLAUDE_COMMAND", "claude").strip() or "claude",
        claude_model=os.getenv("ANALYST_CLAUDE_MODEL", "sonnet").strip() or "sonnet",
        claude_timeout_seconds=float(os.getenv("ANALYST_CLAUDE_TIMEOUT_SECONDS", "300")),
    )


PROVIDERS = ("ollama", "claude_cli")


def _provider(value: str) -> str:
    value = value.strip().lower().replace("-", "_")
    return value if value in PROVIDERS else "ollama"
