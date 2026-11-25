"""A small client for Ollama's local chat API (https://github.com/ollama/ollama/blob/main/docs/api.md)."""
from __future__ import annotations

import httpx

from app.analyst.settings import AnalystSettings


class AnalystUnavailable(Exception):
    """Ollama is not running, the model is not pulled, or it did not answer in time. The message is for the user."""


async def status(settings: AnalystSettings, client: httpx.AsyncClient | None = None) -> dict:
    """Is Ollama reachable, and is the configured model pulled?"""
    info = {"enabled": settings.enabled, "model": settings.model, "ollama_url": settings.ollama_url,
            "ollama_running": False, "model_ready": False, "installed_models": []}
    if not settings.enabled:
        info["detail"] = "The AI analyst is turned off (ANALYST_ENABLED=false)."
        return info
    owns = client is None
    client = client or httpx.AsyncClient(timeout=5)
    try:
        response = await client.get(f"{settings.ollama_url}/api/tags")
        response.raise_for_status()
        names = [m.get("name", "") for m in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        info["detail"] = f"Ollama is not running at {settings.ollama_url}. Start the Ollama app, then try again."
        return info
    finally:
        if owns:
            await client.aclose()
    info["ollama_running"] = True
    info["installed_models"] = names
    wanted = settings.model if ":" in settings.model else f"{settings.model}:latest"
    info["model_ready"] = wanted in names
    info["detail"] = ("ready" if info["model_ready"]
                      else f"Model {settings.model} is not downloaded. Run: ollama pull {settings.model}")
    return info


async def chat(settings: AnalystSettings, messages: list[dict], tools: list[dict] | None = None,
               client: httpx.AsyncClient | None = None) -> dict:
    """One /api/chat call (no streaming). Returns Ollama's answer: {"message": {...}, "eval_count": ...}."""
    body = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
        # num_predict caps the answer, so the case facts, a lookup and the answer still fit in num_ctx.
        "options": {"num_ctx": settings.num_ctx, "temperature": 0.2, "num_predict": settings.max_answer_tokens},
        "keep_alive": "15m",
    }
    if tools:
        body["tools"] = tools
    # Always say whether to think: thinking models (e.g. qwen3) reason silently first unless told not to,
    # which made answers about 3x slower in the live test.
    body["think"] = settings.think
    owns = client is None
    client = client or httpx.AsyncClient(timeout=settings.timeout_seconds)
    try:
        response = await client.post(f"{settings.ollama_url}/api/chat", json=body)
        if response.status_code == 400 and "think" in response.text.lower():
            body.pop("think")  # this model has no thinking switch: ask again without it
            response = await client.post(f"{settings.ollama_url}/api/chat", json=body)
        if response.status_code == 404:
            raise AnalystUnavailable(f"Model {settings.model} is not downloaded. Run: ollama pull {settings.model}")
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise AnalystUnavailable(
            f"The local model did not answer within {settings.timeout_seconds:.0f} seconds.") from exc
    except httpx.ConnectError as exc:
        raise AnalystUnavailable(
            f"Ollama is not running at {settings.ollama_url}. Start the Ollama app, then try again.") from exc
    except httpx.HTTPStatusError as exc:
        raise AnalystUnavailable(f"Ollama answered with an error ({exc.response.status_code}).") from exc
    finally:
        if owns:
            await client.aclose()
