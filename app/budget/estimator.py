"""Local token estimation for reservations.

The estimate is a deliberately conservative upper bound used for reservation
only. It is never reported as usage; actual upstream usage replaces it during
reconciliation. Prompt *content* is measured but never stored or logged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenEstimateConfig:
    chars_per_token: int = 4
    message_overhead_tokens: int = 8
    default_max_completion_tokens: int = 1024
    min_reservation_tokens: int = 32


@dataclass(frozen=True)
class TokenEstimate:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenEstimator:
    """Estimates the maximum token requirement of a chat completion request."""

    def __init__(self, config: TokenEstimateConfig | None = None) -> None:
        self._config = config or TokenEstimateConfig()

    @property
    def config(self) -> TokenEstimateConfig:
        return self._config

    def estimate(self, payload: dict[str, Any]) -> TokenEstimate:
        cfg = self._config
        prompt = 0
        for message in payload.get("messages") or []:
            prompt += cfg.message_overhead_tokens
            prompt += self._text_size(message.get("content")) + self._text_size(message.get("role"))
            for extra_key in ("name", "tool_call_id"):
                prompt += self._text_size(message.get(extra_key))
            if message.get("tool_calls"):
                prompt += self._text_size(message.get("tool_calls"))
        if payload.get("tools"):
            prompt += self._text_size(payload.get("tools"))
        prompt = max(prompt, 0)

        completion = self._completion_bound(payload)
        total = max(prompt + completion, cfg.min_reservation_tokens)
        if total == cfg.min_reservation_tokens and prompt + completion == 0:
            return TokenEstimate(prompt_tokens=0, completion_tokens=0)
        return TokenEstimate(prompt_tokens=prompt, completion_tokens=completion)

    def _completion_bound(self, payload: dict[str, Any]) -> int:
        cfg = self._config
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            value = payload.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if value > 0:
                return value
        return cfg.default_max_completion_tokens

    def _text_size(self, value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(value)
        chars = max(len(text), 0)
        divisor = max(self._config.chars_per_token, 1)
        return chars // divisor
