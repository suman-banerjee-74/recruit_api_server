"""Authenticated HTTP routes; HTTP handling stays outside Oracle business logic."""
import base64
import binascii
import hashlib
import json
import secrets
import time
from contextlib import contextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from recruit_server.http.logging import RequestLogging
from recruit_server.infrastructure.cache import Cache
from recruit_server.core.config import Settings
from recruit_server.core.errors import APIError, invalid
from recruit_server.core.identifiers import public_row
from recruit_server.infrastructure.oracle import OracleClient, envelope
from recruit_server.services.recruiting import Recruiting
from recruit_server.services.resumes import Resumes, remove_file


class Security:
    """ASGI boundary protects every route and suppresses sensitive exception output."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        response_started = False
        async def safe_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-elapsed-ms", str(round((time.perf_counter() - started) * 1000, 3)).encode()),
                ]
            await send(message)
        try:
            headers = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
            if len(headers) != 1:
                raise ValueError
            scheme, encoded = headers[0].split(b" ", 1)
            if scheme.lower() != b"basic" or len(encoded) > 8192:
                raise ValueError
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            user, password = decoded.split(":", 1)
            # Hashes give fixed-length comparisons, including non-ASCII passwords.
            valid_user = secrets.compare_digest(hashlib.sha256(user.encode()).digest(),
                                                hashlib.sha256(self.settings.fusion_username.encode()).digest())
            valid_password = secrets.compare_digest(hashlib.sha256(password.encode()).digest(),
                                                    hashlib.sha256(self.settings.fusion_password.encode()).digest())
            if not (valid_user and valid_password):
                raise ValueError
        except (ValueError, UnicodeDecodeError, binascii.Error):
            response = JSONResponse({"error": {"code": "unauthorized", "message": "Basic authentication required"}}, 401,
                                    headers={"WWW-Authenticate": 'Basic realm="Recruit API", charset="UTF-8"'})
            return await response(scope, receive, safe_send)
        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            # Starlette re-raises unexpected exceptions after its sanitized 500.
            # Keep raw exception messages out of the ASGI server's error logs too.
            if not response_started:
                await JSONResponse({"error": {"code": "internal_error", "message": "Request failed"}}, 500)(scope, receive, safe_send)


class Options:
    def __init__(self, request, allowed):
        self.query = request.query_params
        if set(self.query) - set(allowed) or len(self.query.multi_items()) != len(self.query):
            raise invalid("Unknown or repeated query parameter")

    def alias(self, key, alias):
        if key in self.query and alias in self.query:
            raise invalid("Specify only one of " + key + " and " + alias)
        value = self.query.get(key, self.query.get(alias))
        if value is not None and not value.strip():
            raise invalid("Phase and state filters cannot be empty")
        return value

    def bool(self, key, default=False):
        value = self.query.get(key, str(default).lower())
        if value not in ("true", "false"):
            raise invalid("Boolean query parameters must be true or false")
        return value == "true"

    def integer(self, key, default, lower, upper):
        value = self.query.get(key, str(default))
        if not value.isascii() or not value.isdigit() or not lower <= int(value) <= upper:
            raise invalid("Pagination values are outside their permitted bounds")
        return int(value)

    def paging(self, settings):
        return {"limit": self.integer("limit", settings.page_size, 1, 500),
                "offset": self.integer("offset", 0, 0, 1000000000),
                "retrieve_all": self.bool("retrieve_all", True)}


PAGING = ("limit", "offset", "retrieve_all")
FILTERS = ("phase", "state", "PhaseName", "StateName", "modified_since", "hiring_manager_person_number", "recruiter_person_number", "resolve_references")
DETAILS = ("include_children", "include_descriptions", "resolve_references")


class PrivateFileResponse(FileResponse):
    """Remove the private temporary file on normal completion or disconnect."""
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            remove_file(self.path)


def validate_move(body):
    required = {"application_id", "phase_id", "state_id", "expected_phase_id", "expected_state_id"}
    optional = {"reason_id", "comments"}
    if not isinstance(body, dict) or not required <= body.keys() or body.keys() - required - optional:
        raise invalid("Move requires application_id, phase_id, state_id, expected_phase_id, and expected_state_id")
    for key in required | ({"reason_id"} if "reason_id" in body else set()):
        if type(body[key]) is not int or not 0 < body[key] < 2 ** 63:
            raise invalid("Move IDs must be positive 64-bit integers")
    if "comments" in body and (not isinstance(body["comments"], str) or len(body["comments"]) > 4000):
        raise invalid("Move comments must be text of at most 4000 characters")
    return body


def create_app(settings=None, *, client_factory=None):
    settings = settings or Settings.from_env()
    cache = Cache(settings.cache_ttl, settings.cache_entries, settings.cache_bytes)
    factory = client_factory or OracleClient

    @contextmanager
    def service():
        # A requests session is private to one HTTP invocation; the cache is shared.
        client = factory(settings, cache)
        try:
            yield Recruiting(client), Resumes(client)
        finally:
            client.close()

    def health(request):
        Options(request, ())
        return JSONResponse({"status": "ok", "oracle_connectivity": "not_probed"})

    def capabilities(request):
        Options(request, ())
        return JSONResponse({"authentication": "Basic", "oracle_authentication": "Basic",
                             "journey_updates_enabled": settings.enable_updates,
                             "journey_history": "unavailable", "tenant_verification": "unverified",
                             "default_retrieve_all": True, "default_page_size": settings.page_size,
                             "max_pages": settings.max_pages, "max_items": settings.max_items,
                             "cache_ttl_seconds": settings.cache_ttl})

    def list_requisitions(request):
        options = Options(request, PAGING + FILTERS)
        phase = options.alias("phase", "PhaseName")
        state = options.alias("state", "StateName")
        if request.url.path.endswith("/open") and phase not in (None, "Open"):
            raise invalid("The open requisitions endpoint requires phase Open")
        if ("hiring_manager" in request.path_params and "hiring_manager_person_number" in options.query
                or "recruiter" in request.path_params and "recruiter_person_number" in options.query):
            raise invalid("A manager path parameter cannot be overridden in the query")
        for key in ("hiring_manager_person_number", "recruiter_person_number"):
            if key in options.query and not options.query[key].strip():
                raise invalid("Person-number filters cannot be empty")
        with service() as (recruiting, _):
            result = recruiting.requisitions(
                phase=phase if phase is not None else ("Open" if request.url.path.endswith("/open") else None),
                state=state, modified_since=options.query.get("modified_since"),
                hiring_manager=options.query.get("hiring_manager_person_number") or request.path_params.get("hiring_manager"),
                recruiter=options.query.get("recruiter_person_number") or request.path_params.get("recruiter"),
                resolve_references=options.bool("resolve_references", True), **options.paging(settings),
            )
        return collection_response(result)

    def detail(request):
        options = Options(request, DETAILS)
        with service() as (recruiting, _):
            result = recruiting.requisition(request.path_params.get("number"), key=request.path_params.get("key"),
                                            **{key: options.bool(key, key == "resolve_references") for key in DETAILS})
        return JSONResponse(result)

    def statuses(request):
        options = Options(request, PAGING)
        with service() as (recruiting, _):
            result = recruiting.statuses(**options.paging(settings))
        return collection_response(result)

    def requisition_candidates(request):
        options = Options(request, PAGING + ("include_children", "include_profiles"))
        with service() as (recruiting, _):
            result = recruiting.applications(request.path_params["number"],
                                             include_children=options.bool("include_children"),
                                             include_profiles=options.bool("include_profiles"), **options.paging(settings))
        return collection_response(result)

    def journey(request):
        options = Options(request, PAGING)
        with service() as (recruiting, _):
            result = recruiting.journey(request.path_params["number"], request.path_params["candidate_number"], **options.paging(settings))
        return collection_response(result)

    async def move(request):
        Options(request, ())
        if not settings.enable_updates:
            raise APIError("updates_disabled", "Journey updates are disabled in server configuration", 403)
        if request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
            raise APIError("content_type", "Use application/json for journey updates", 415)
        # Bound request bodies while reading, even if Content-Length is missing.
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 16384:
                raise APIError("body_limit", "Request body exceeds 16 KiB", 413)
        try:
            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result
            body = validate_move(json.loads(content, object_pairs_hook=unique_pairs))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise invalid("Invalid JSON move request") from None
        def execute():
            with service() as (recruiting, _):
                return recruiting.move(request.path_params["number"], request.path_params["candidate_number"], body)
        return JSONResponse(await run_in_threadpool(execute))

    def candidates(request):
        options = Options(request, PAGING + ("keywords", "last_name", "candidate_type", "modified_since"))
        with service() as (recruiting, _):
            result = recruiting.candidates(**{key: options.query.get(key) for key in
                                              ("keywords", "last_name", "candidate_type", "modified_since")},
                                           **options.paging(settings))
        return collection_response(result)

    def candidate_history(request):
        options = Options(request, PAGING + ("identity_type",))
        with service() as (recruiting, _):
            result = recruiting.candidate_history(
                request.path_params["candidate_number"],
                identity_type=options.query.get("identity_type", "candidate_number"),
                **options.paging(settings))
        return collection_response(result)

    def candidate(request):
        Options(request, ())
        with service() as (recruiting, _):
            result = envelope(public_row(recruiting.candidate(request.path_params["candidate_number"])), "recruitingCandidates")
        return JSONResponse(result)

    def candidate_summary(request):
        options = Options(request, PAGING)
        with service() as (recruiting, _):
            result = recruiting.candidate_summary(request.path_params["candidate_number"], **options.paging(settings))
        return collection_response(result)

    def candidate_details(request):
        options = Options(request, PAGING)
        with service() as (recruiting, _):
            result = recruiting.candidate_details(request.path_params["candidate_number"], request.path_params["child"], **options.paging(settings))
        return collection_response(result)

    def resumes(request):
        options = Options(request, PAGING + ("include_hints",))
        with service() as (_, resume_service):
            result = resume_service.list(request.path_params["candidate_number"], include_hints=options.bool("include_hints"), **options.paging(settings))
        return collection_response(result)

    def download(request):
        options = Options(request, ("include_hints",))
        archive = "key" not in request.path_params
        with service() as (_, resume_service):
            if archive:
                path = resume_service.archive(request.path_params["candidate_number"], include_hints=options.bool("include_hints"))
            else:
                path = resume_service.content(request.path_params["candidate_number"], request.path_params["key"], include_hints=options.bool("include_hints"))
        return PrivateFileResponse(path, media_type="application/zip" if archive else "application/octet-stream",
                                   filename="resumes.zip" if archive else "resume.bin")

    async def api_error(request, exc):
        return JSONResponse({"error": exc.public()}, exc.status)

    async def http_error(request, exc):
        return JSONResponse({"error": {"code": "http_error", "message": "Route or HTTP method is not available"}}, exc.status_code)

    async def unexpected(request, exc):
        # Do not expose exception reprs, request URLs, credentials, or Oracle data.
        return JSONResponse({"error": {"code": "internal_error", "message": "Request failed"}}, 500)

    routes = [
        Route("/health", health), Route("/capabilities", capabilities),
        Route("/requisitions", list_requisitions), Route("/requisitions/open", list_requisitions),
        Route("/requisitions/statuses", statuses),
        Route("/hiring-managers/{hiring_manager}/requisitions", list_requisitions),
        Route("/recruiters/{recruiter}/requisitions", list_requisitions),
        Route("/requisitions/by-key/{key}", detail), Route("/requisitions/{number}", detail),
        Route("/requisitions/{number}/candidates", requisition_candidates),
        Route("/requisitions/{number}/candidates/{candidate_number}/journey", journey),
        Route("/requisitions/{number}/candidates/{candidate_number}/journey", move, methods=["POST"]),
        Route("/candidates", candidates), Route("/candidates/{candidate_number}", candidate),
        Route("/candidates/{candidate_number}/history", candidate_history),
        Route("/candidates/{candidate_number}/details", candidate_summary),
        Route("/candidates/{candidate_number}/details/{child}", candidate_details),
        Route("/candidates/{candidate_number}/resumes", resumes),
        Route("/candidates/{candidate_number}/resumes/archive", download),
        Route("/candidates/{candidate_number}/resumes/{key}/content", download),
    ]
    # Compatibility aliases share handlers and validation; new clients use /candidates.
    candidate_routes = [route for route in routes if "/candidates/" in route.path
                        or route.path == "/requisitions/{number}/candidates"]
    routes.extend(Route(route.path.replace("/candidates", "/applicants"), route.endpoint,
                        methods=route.methods, name="legacy_" + route.name)
                  for route in candidate_routes)
    from starlette.exceptions import HTTPException
    app = Starlette(routes=routes, exception_handlers={APIError: api_error, HTTPException: http_error, Exception: unexpected})
    app.state.cache, app.state.settings = cache, settings
    # Wrap outside Starlette's error middleware so all responses get security headers.
    return RequestLogging(Security(app, settings), routes)


def collection_response(result):
    # Retained rows are usable even after a later page fails. Inspect metadata.
    status = 502 if result["metadata"]["retrieval_status"] == "unavailable" else 200
    return JSONResponse(result, status)
