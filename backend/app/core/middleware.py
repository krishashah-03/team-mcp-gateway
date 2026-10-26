import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.logging import request_id_var
from app.core.metrics import metrics

logger = logging.getLogger("app.request")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class HardeningMiddleware:
    """Caps request size and adds conservative security headers to responses."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        declared = Request(scope).headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.max_body_bytes:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                scope, receive, send
            )
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Cache-Control", "no-store")
                headers.setdefault("Referrer-Policy", "no-referrer")
            await send(message)

        await self._app(scope, receive, send_with_headers)


class RequestLogMiddleware:
    """Gives every request an id, logs one line per request, and counts it.

    Only the method, path, status and duration are logged. Query strings, headers and
    bodies are never logged, so keys and tokens cannot leak through the access log.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        supplied = Request(scope).headers.get("x-request-id", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.monotonic()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self._app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            duration_ms = int((time.monotonic() - started) * 1000)
            metrics.inc(
                "gateway_http_requests_total",
                method=scope.get("method", "?"),
                status=f"{status_code // 100}xx",
            )
            if path != "/health":
                logger.info(
                    "%s %s -> %s (%d ms)",
                    scope.get("method", "?"),
                    path,
                    status_code,
                    duration_ms,
                    extra={
                        "fields": {
                            "method": scope.get("method"),
                            "path": path,
                            "status": status_code,
                            "duration_ms": duration_ms,
                        }
                    },
                )
            request_id_var.reset(token)
