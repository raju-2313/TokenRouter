"""OpenAI-compatible routes.

The route layer stays thin: parse/validate, hand off to the service layer, and
serialize the outcome. It performs no account selection, no budget arithmetic
and no upstream calls for chat completions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_chat_service, get_registry, get_upstream_client
from app.api.schemas import ChatCompletionRequest
from app.core.obs import log_event
from app.core.request_id import new_request_id
from app.models.discovery import discover_models
from app.upstream.errors import UPSTREAM_ERROR, UpstreamError

router = APIRouter()

# Optional pin: send this header to force a specific configured account. The
# router never silently moves a pinned request to a different account.
ACCOUNT_HEADER = "x-tokenrouter-account"


@router.get("/v1/models")
async def list_models() -> JSONResponse:
    registry = get_registry()
    client = get_upstream_client()
    try:
        await discover_models(client, registry)
    except UpstreamError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    except ValueError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": {"code": UPSTREAM_ERROR, "message": str(exc)}},
        )
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=502,
            content={"error": {"code": UPSTREAM_ERROR, "message": "Failed to fetch models"}},
        )
    body = registry.to_openai_list()
    return JSONResponse(status_code=200, content=body)


@router.post("/v1/chat/completions")
async def chat_completions(req: Request) -> JSONResponse:
    request_id = new_request_id()
    try:
        raw = await req.json()
    except Exception:  # noqa: BLE001
        return _error_response(400, "INVALID_REQUEST", "Invalid JSON body", request_id, log=False)
    try:
        parsed = ChatCompletionRequest.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            400, "INVALID_REQUEST", f"Invalid request: {exc}", request_id, log=False
        )
    if parsed.stream:
        return _error_response(
            400,
            "INVALID_REQUEST",
            "Streaming not yet supported in M1 — use stream: false",
            request_id,
        )
    if not isinstance(raw, dict):
        return _error_response(
            400, "INVALID_REQUEST", "Invalid request body", request_id, log=False
        )

    service = await get_chat_service()
    outcome = await service.handle_chat(
        request_id=request_id,
        payload=raw,
        model=parsed.model,
        pinned_account_id=req.headers.get(ACCOUNT_HEADER),
    )
    return _outcome_response(outcome)


def _outcome_response(outcome: Any) -> JSONResponse:
    body = outcome.body
    error = outcome.error
    retry_after = getattr(error, "retry_after", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body["error"]["request_id"] = outcome.request_id
        if retry_after is not None:
            body["error"]["retry_after"] = retry_after
    response = JSONResponse(status_code=outcome.status_code, content=body)
    response.headers["x-request-id"] = outcome.request_id
    if retry_after is not None:
        response.headers["retry-after"] = str(retry_after)
    return response


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    *,
    log: bool = True,
) -> JSONResponse:
    if log:
        log_event(
            "chat_completion",
            request_id=request_id,
            http_status=status_code,
            error_code=code,
        )
    response = JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )
    response.headers["x-request-id"] = request_id
    return response
