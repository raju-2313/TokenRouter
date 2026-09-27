"""Pydantic schemas for OpenAI-compatible gateway."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: Any  # str | list[dict] for vision etc — preserve as-is
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: Any | None = None
    reasoning: Any | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    tools: Any | None = None
    tool_choice: Any | None = None
    # Accept arbitrary additional fields (reasoning, vision, etc.)
    model_config = {"extra": "allow"}


class UsageMeta(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class RequestMetaLog(BaseModel):
    request_id: str
    model: str | None
    stream: bool
    http_status: int
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
