"""Explain a case, or answer questions about it, with the local model."""
from __future__ import annotations

import asyncio
import json
import re
import time

from app.analyst import claude_cli, ollama
from app.analyst.checks import check_answer
from app.analyst.context import FULL, LOCAL, build_facts
from app.analyst.settings import AnalystSettings
from app.analyst.tools import TOOLS, run_tool

SYSTEM = """You are the AI analyst in a blockchain intelligence dashboard. You help an investigator understand one case.

Rules:
- Use only the facts in the CASE FACTS and in tool results. Never invent addresses, transaction IDs, amounts, dates, labels or scores.
- If something is not in the data, say it is not in the data. Do not guess.
- When you name an address or transaction, copy it exactly from the facts.
- For who paid whom, use "money_flow_with_subject", "subject_side" and the link text exactly. Never reverse a direction.
- Give exact counts from the facts, such as "1 of 25 counterparties" (see "list_matches"). Never write "many", "several" or "a high number" for a count.
- For a list match, say which list (for example "on the OFAC SDN list"). Do not call a listed address "illicit". Use "illicit label" only for addresses in "known_illicit_counterparties", and say it is a training label.
- Explain a model feature from its "meaning". A _min or _max feature is one extreme case (for example the single shortest gap), not the usual pattern.
- The model score is a pattern match against 2016-2017 Elliptic++ labels, not proof of a crime. Red flags and list matches are leads to check, not conclusions.
- Be concise and plain. Use short paragraphs or bullet points. No preamble."""

# Claude gets the whole case and no length limit (Puneet, 2026-11-24: "as much as possible to get the best results").
SYSTEM_FULL = SYSTEM.replace(
    "- Be concise and plain. Use short paragraphs or bullet points. No preamble.",
    "- Be thorough and plain: use as much of the data as helps the investigator, with headings and bullet points."
    " No preamble.")

EXPLAIN_TASK = """Explain this case to the investigator in under 220 words:
1. One sentence on what was looked up and the overall picture.
2. The model score: the exact score, the threshold, whether it is above or below, and the 2-3 SHAP features that mattered most, in plain words.
3. The red flags, rules and list matches that fired (or say that none did).
4. Two or three concrete things the investigator should check next.
Mention limitations only if they change the conclusion."""

EXPLAIN_TASK_FULL = """Write a full investigation briefing for the investigator. There is no length limit.
1. What was looked up and the overall picture, in two or three sentences.
2. The model score: the exact score, the threshold, above or below, and every SHAP feature listed, in plain words.
3. Every red flag, rule and list match that fired, and how many rules were clear or not run.
4. The money flows: who paid the subject and whom the subject paid, with amounts, dates and transactions, the
   largest and the listed counterparties first, and what the expanded graph and any older transactions add.
5. Patterns worth noting (timing, round amounts, groups, repeated counterparties), each tied to the facts.
6. A prioritised list of concrete next steps.
7. The limitations that matter for the conclusion."""

# The GPU runs one answer at a time; more requests would only queue inside Ollama and time out.
_gpu = asyncio.Semaphore(1)


def _clean(text: str) -> str:
    """Drop any <think>...</think> block a model wrote into its answer."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def _facts_message(facts: dict) -> dict:
    return {"role": "user", "content": "CASE FACTS (JSON):\n" + json.dumps(facts, default=str, ensure_ascii=False)}


def _stats(reply: dict) -> dict:
    duration = reply.get("eval_duration") or 0
    return {"output_tokens": reply.get("eval_count"), "prompt_tokens": reply.get("prompt_eval_count"),
            "tokens_per_second": round(reply["eval_count"] / (duration / 1e9), 1)
            if duration and reply.get("eval_count") else None}


async def _claude(settings: AnalystSettings, messages: list[dict], evidence: list, started: float) -> dict:
    """The same answer, written by Claude through the Claude CLI (no tools; the facts are already in the prompt)."""
    reply = await claude_cli.chat(settings, messages)
    answer = _clean(reply["text"])
    if not answer:
        raise ollama.AnalystUnavailable("Claude returned an empty answer. Try again.")
    return {"answer": answer, "model": reply["model"], "provider": "claude_cli",
            "seconds": round(time.perf_counter() - started, 1), "checks": check_answer(answer, evidence),
            "tools_used": [], "output_tokens": reply["output_tokens"], "prompt_tokens": reply["prompt_tokens"],
            "tokens_per_second": reply["tokens_per_second"]}


async def explain(settings: AnalystSettings, result: dict, provider_name: str | None = None) -> dict:
    started = time.perf_counter()
    if (provider_name or settings.provider) == "claude_cli":
        facts = build_facts(result, FULL)
        messages = [{"role": "system", "content": SYSTEM_FULL}, _facts_message(facts),
                    {"role": "user", "content": EXPLAIN_TASK_FULL}]
        return await _claude(settings, messages, [facts], started)
    facts = build_facts(result, LOCAL)
    messages = [{"role": "system", "content": SYSTEM}, _facts_message(facts),
                {"role": "user", "content": EXPLAIN_TASK}]
    async with _gpu:
        reply = await ollama.chat(settings, messages)
    answer = _clean(reply.get("message", {}).get("content", ""))
    if not answer:
        raise ollama.AnalystUnavailable("The local model returned an empty answer. Try again.")
    return {"answer": answer, "model": settings.model, "provider": "ollama",
            "seconds": round(time.perf_counter() - started, 1),
            "checks": check_answer(answer, [facts]), "tools_used": [], **_stats(reply)}


async def chat(settings: AnalystSettings, result: dict, history: list[dict], provider, indicators,
               provider_name: str | None = None) -> dict:
    """Answer the newest question in history ([{"role": "user"|"assistant", "content": str}, ...]).

    `provider` is the Bitcoin data provider for the lookup tools; `provider_name` picks who answers
    ("ollama" or "claude_cli", default from settings). Claude answers from the fact sheet without the lookup tools.
    """
    claude = (provider_name or settings.provider) == "claude_cli"
    facts = build_facts(result, FULL if claude else LOCAL)
    messages = [{"role": "system", "content": SYSTEM_FULL if claude else SYSTEM}, _facts_message(facts),
                {"role": "assistant", "content": "I have the case facts. What would you like to know?"}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    evidence: list = [facts]
    tools_used = []
    started = time.perf_counter()
    if claude:
        return await _claude(settings, messages, evidence + [m["content"] for m in history if m["role"] == "user"],
                             started)
    async with _gpu:
        reply = {}
        for round_number in range(settings.max_tool_rounds + 1):
            offer_tools = round_number < settings.max_tool_rounds
            reply = await ollama.chat(settings, messages, tools=TOOLS if offer_tools else None)
            message = reply.get("message", {})
            calls = message.get("tool_calls") or []
            if not calls or not offer_tools:
                break
            messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": calls})
            for call in calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                output = await run_tool(name, arguments, result, provider, indicators)
                tools_used.append({"name": name, "arguments": arguments, "ok": "error" not in output})
                evidence.append(output)
                messages.append({"role": "tool", "tool_name": name,
                                 "content": json.dumps(output, default=str, ensure_ascii=False)})
    answer = _clean(reply.get("message", {}).get("content", ""))
    if not answer:
        raise ollama.AnalystUnavailable("The local model returned an empty answer. Try asking again.")
    return {"answer": answer, "model": settings.model, "provider": "ollama",
            "seconds": round(time.perf_counter() - started, 1),
            "checks": check_answer(answer, evidence + [m["content"] for m in history if m["role"] == "user"]),
            "tools_used": tools_used, **_stats(reply)}
