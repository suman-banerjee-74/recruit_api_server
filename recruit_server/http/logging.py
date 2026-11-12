"""ASGI request lifecycle logging without raw URLs, headers, or payloads."""
import time
from uuid import uuid4

from recruit_server.core.observability import event, request_id


class RequestLogging:
    def __init__(self, app, routes=()):
        self.app = app
        self.routes = tuple(routes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        correlation = uuid4().hex
        token = request_id.set(correlation)
        started, status, outcome = time.perf_counter(), 500, "completed"
        method = scope.get("method")
        method = method if method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS") else "OTHER"

        async def logged_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", correlation.encode("ascii")),
                ]
            await send(message)

        event("request_started", method=method)
        try:
            await self.app(scope, receive, logged_send)
        except BaseException:
            outcome = "interrupted"
            raise
        finally:
            # Use only registered route templates, never the incoming URL's record values.
            template = next((route.path for route in self.routes
                             if route.path_regex.fullmatch(scope.get("path", ""))), "unmatched")
            event("request_finished", method=method, route=template,
                  status=status, outcome=outcome, elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
            request_id.reset(token)
