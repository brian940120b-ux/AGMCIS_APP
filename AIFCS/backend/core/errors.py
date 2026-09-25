"""One error shape, and one thread to pull on when something breaks (PHASE 20).

Before this, an unhandled exception returned the string ``Internal Server
Error`` with no body structure. Nothing leaked, which was the important part,
but two things were missing:

**The dashboard could not say what went wrong.** Every deliberate refusal in
this API explains itself in ``detail`` — "a simulation is running", "above the
configured cap" — and the client reads that field. A plain-text 500 reaching the
same code path produced "GET /api/… failed (500)" and nothing else.

**Nothing connected the response to the log.** The traceback was in the server
log and the operator had a 500; joining them meant guessing by timestamp. Every
response now carries a ``request_id`` that appears in the log line, so an
operator with a failure can quote one string and have the whole traceback found.

What is deliberately *not* returned is the exception's own text. A message can
carry a filesystem path, a configuration value or part of a payload, and the
person holding the request id is not necessarily the person who should see
those. The id is the handle; the log has the detail.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging_config import get_logger

log = get_logger("api.errors")

# Header carrying the id, so a proxy log and an application log can be joined.
REQUEST_ID_HEADER = "X-AIFCS-Request-Id"


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def error_body(detail: str, request_id: str, **extra: Any) -> dict[str, Any]:
    """The one shape every error response takes.

    ``detail`` stays the field it has always been, because the dashboard and
    every test already read it and a consistent error is worth more than a
    tidier name.
    """
    return {"detail": detail, "request_id": request_id, **extra}


def install_error_handlers(app: FastAPI) -> None:
    """Give an app the shared error contract."""

    @app.middleware("http")
    async def _tag_request(request: Request, call_next: Any) -> Any:
        request_id = new_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # A deliberate refusal. Its detail was written to be read, so it is
        # passed through untouched.
        request_id = getattr(request.state, "request_id", new_request_id())
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(str(exc.detail), request_id),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", new_request_id())
        # Pydantic's own errors are precise and safe to show: they name the
        # field and the rule, not the environment.
        problems = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                "problem": error.get("msg", "invalid"),
            }
            for error in exc.errors()
        ]
        summary = "; ".join(f"{p['field'] or 'body'}: {p['problem']}" for p in problems)
        return JSONResponse(
            status_code=422,
            content=error_body(
                f"That request does not fit the API: {summary}", request_id, problems=problems
            ),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", new_request_id())
        # The whole exception goes to the log, where an operator can reach it.
        log.exception(
            "unhandled error",
            extra={
                "event": "UNHANDLED_ERROR",
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
            },
        )
        return JSONResponse(
            status_code=500,
            content=error_body(
                "Something went wrong inside AIFCS. The failure is in the backend log "
                f"under request id {request_id}.",
                request_id,
            ),
            headers={REQUEST_ID_HEADER: request_id},
        )
