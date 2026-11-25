"""AI analyst routes (Milestone 14). The model runs locally in Ollama; see app/analyst.

GET  /api/analyst/status   is Ollama running, and is the model downloaded?
POST /api/analyst/explain  a plain-English explanation of one investigation result
POST /api/analyst/chat     answer a question about it (may call read-only lookup tools)

Questions and answers are not saved or logged.
"""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.analyst import claude_cli, ollama, service
from app.analyst.settings import analyst_settings
from app.auth.accounts import CurrentUser
from app.auth.deps import current_user
from app.auth.settings import auth_settings
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.providers.base import BitcoinProvider
from app.providers.registry import get_bitcoin_provider

router = APIRouter(prefix="/api/analyst", tags=["ai analyst"])

MAX_RESULT_BYTES = 5_000_000


async def analyst_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Analysts and admins; anyone on a local copy with sign-in turned off (AUTH_REQUIRED=false)."""
    if user.id is None and not auth_settings().required:
        return user
    if user.id is None or not user.has("analyst"):
        raise HTTPException(status_code=403, detail="Only analysts and admins can use the AI analyst.")
    return user


class CaseBody(BaseModel):
    # The investigation result exactly as the dashboard holds it (with the model score and red flags merged in).
    investigation: dict
    # Who answers: "ollama" (local model) or "claude_cli" (sends the case facts to Anthropic). Default: ANALYST_PROVIDER.
    provider: Literal["ollama", "claude_cli"] | None = None

    @field_validator("investigation")
    @classmethod
    def _not_huge(cls, value: dict) -> dict:
        if len(json.dumps(value, default=str)) > MAX_RESULT_BYTES:
            raise ValueError("This investigation is too large for the AI analyst.")
        if "graph" not in value or "findings" not in value:
            raise ValueError("Send an investigation result (it needs findings and a graph).")
        return value


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatBody(CaseBody):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)

    @field_validator("messages")
    @classmethod
    def _ends_with_question(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if value[-1].role != "user":
            raise ValueError("The last message must be the analyst's question.")
        return value


@router.get("/status")
async def status(_: CurrentUser = Depends(current_user)) -> dict:
    """The local model's status (top level, as before), plus the default provider and the Claude CLI's status."""
    settings = analyst_settings()
    info = await ollama.status(settings)
    info["provider"] = settings.provider
    info["claude_cli"] = await claude_cli.status(settings) if settings.enabled else {"ready": False}
    return info


def _settings_or_503():
    settings = analyst_settings()
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="The AI analyst is turned off (ANALYST_ENABLED=false).")
    return settings


@router.post("/explain")
async def explain(body: CaseBody, _: CurrentUser = Depends(analyst_user)) -> dict:
    try:
        return await service.explain(_settings_or_503(), body.investigation, body.provider)
    except ollama.AnalystUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/chat")
async def chat(
    body: ChatBody,
    _: CurrentUser = Depends(analyst_user),
    provider: BitcoinProvider = Depends(get_bitcoin_provider),
    indicators: IndicatorIndex = Depends(get_indicator_index),
) -> dict:
    history = [m.model_dump() for m in body.messages]
    try:
        return await service.chat(_settings_or_503(), body.investigation, history, provider, indicators,
                                  body.provider)
    except ollama.AnalystUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
