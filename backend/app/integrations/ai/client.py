import json
import re
from pathlib import Path
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import Settings


class TranscriptionResult(BaseModel):
    text: str
    language: str | None = None
    duration_seconds: float | None = None


class TranscriptionProvider(Protocol):
    async def transcribe(self, file_path: Path, language_hint: str | None = None) -> TranscriptionResult:
        ...


class OpenAICompatibleTranscriptionProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.ai_api_key or "missing", base_url=settings.ai_base_url)

    async def transcribe(self, file_path: Path, language_hint: str | None = None) -> TranscriptionResult:
        if not self.settings.ai_transcription_model:
            raise RuntimeError("AI_TRANSCRIPTION_MODEL is not configured")
        with file_path.open("rb") as audio:
            kwargs = {"model": self.settings.ai_transcription_model, "file": audio}
            if language_hint:
                kwargs["language"] = language_hint
            result = await self.client.audio.transcriptions.create(**kwargs)
        return TranscriptionResult(text=getattr(result, "text", ""), language=language_hint)


def chat_model(settings: Settings) -> ChatOpenAI | None:
    if not settings.ai_api_key or not settings.ai_chat_model:
        return None
    return ChatOpenAI(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_chat_model,
        timeout=settings.ai_timeout_seconds,
        max_retries=settings.ai_max_retries,
        temperature=settings.ai_temperature,
        max_tokens=settings.ai_max_tokens,
    )


async def structured_json(model: ChatOpenAI | None, system: str, user: str, fallback: dict[str, object]) -> dict[str, object]:
    if model is None:
        return fallback
    response = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    content = str(response.content)
    fenced = re.search(r"```(?:json)?\s*(?P<body>.*?)\s*```", content, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        content = fenced.group("body")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass
        return fallback
