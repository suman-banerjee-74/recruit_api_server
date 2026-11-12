"""Oracle HTTP boundary: bounded GET retries, safe paging, one isolated POST."""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests

from recruit_server.core.errors import APIError, invalid
from recruit_server.core.observability import event
from recruit_server.core.identifiers import identifier


NOTICE = "Oracle's Job Applications category carries an internal-use notice; external-use support is unverified."


def envelope(data, source, **metadata):
    return {"data": data, "metadata": {
        "source": source.split("/")[0], "tenant_verification": "unverified",
        "visibility": "configured_oracle_account", "is_complete": False,
        "retrieval_status": "retrieved", "errors": [],
        **({"support_notice": NOTICE} if source.startswith("recruitingJobApplications") else {}),
        **metadata,
    }}


class OracleClient:
    def __init__(self, settings, cache, session=None):
        self.settings, self.cache = settings, cache
        self.base = settings.resource_base
        self.session = session or requests.Session()
        # Avoid implicit .netrc credentials and ambient proxy configuration.
        self.session.trust_env = False
        self.session.auth = (settings.fusion_username, settings.fusion_password)
        self.session.headers.update({"Accept": "application/json", "REST-Framework-Version": "4"})

    def close(self):
        self.session.close()

    def url(self, value, *, relative_to=None):
        try:
            if (not isinstance(value, str) or not value or "\\" in value
                    or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError
            if any(part in (".", "..") for part in urlsplit(value).path.split("/")):
                raise ValueError
            result = urljoin(relative_to or self.base, value)
            parsed, base = urlsplit(result), urlsplit(self.base)
            decoded = parsed.path
            # Reject encoded traversal and encoded path separators, even if nested.
            for _ in range(5):
                expanded = unquote(decoded)
                if expanded == decoded:
                    break
                decoded = expanded
            if (parsed.scheme != "https" or parsed.hostname != base.hostname
                    or (parsed.port or 443) != (base.port or 443)
                    or parsed.username or parsed.password or parsed.fragment
                    or not parsed.path.startswith(base.path)
                    or decoded != parsed.path or "\\" in decoded
                    or any(part in (".", "..") for part in decoded.split("/"))):
                raise ValueError
            return result
        except (ValueError, TypeError):
            raise APIError("unsafe_url", "Rejected an unsafe Oracle resource link", 502) from None

    def item_key(self, row, collection):
        """Accept only a matching self/canonical link, never a numeric ID fallback."""
        links = row.get("links", [])
        if not isinstance(links, list):
            return None
        prefix = urlsplit(self.url(collection)).path + "/"
        for link in links:
            if not isinstance(link, dict) or link.get("rel") not in ("self", "canonical"):
                continue
            try:
                parsed = urlsplit(self.url(link.get("href")))
                if not parsed.query and parsed.path.startswith(prefix):
                    key = parsed.path[len(prefix):]
                    if "/" not in key:
                        return identifier(key)
            except APIError:
                continue
        return None

    def _delay(self, attempt, retry_after=None):
        delay = 0.5 * (2 ** attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except (TypeError, ValueError):
                try:
                    delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        time.sleep(min(self.settings.max_backoff, max(0, delay)))

    def _send(self, path, params=None, *, body=None, binary=False):
        method = "POST" if body is not None else "GET"
        if method == "POST":
            if not self.settings.enable_updates:
                raise APIError("updates_disabled", "Journey updates are disabled in server configuration", 403)
            if not re.fullmatch(r"recruitingJobApplications/[A-Za-z0-9_.:-]+/action/move", path):
                raise invalid("Only the application move action may write to Oracle")
        url = self.url(path)
        attempts = self.settings.max_retries + 1 if method == "GET" else 1
        headers = {"Accept": "application/octet-stream"} if binary else {}
        if body is not None:
            headers["Content-Type"] = "application/vnd.oracle.adf.action+json"
        resource = path.split("/", 1)[0]
        if resource not in {"workers", "publicWorkers", "recruitingJobRequisitions", "recruitingJobApplications",
                            "recruitingCandidates", "recruitingLocationsLOV", "locations", "departmentsLov",
                            "hcmBusinessUnitsLOV"}:
            resource = "other"
        for attempt in range(attempts):
            started = time.perf_counter()
            event("oracle_started", method=method, resource=resource, attempt=attempt + 1)
            try:
                response = self.session.request(
                    method, url, params=params, json=body, headers=headers,
                    timeout=self.settings.timeout, verify=True, allow_redirects=False, stream=True,
                )
            except requests.exceptions.SSLError:
                event("oracle_failed", resource=resource, attempt=attempt + 1, reason="tls_error")
                raise APIError("tls_error", "Oracle TLS verification failed", 502) from None
            except (requests.Timeout, requests.ConnectionError):
                event("oracle_failed", resource=resource, attempt=attempt + 1, reason="connection_or_timeout")
                if attempt + 1 < attempts:
                    self._delay(attempt)
                    continue
                raise APIError("oracle_connection", "Oracle connection failed or timed out", 502,
                               outcome="unknown" if body is not None else None) from None
            except requests.RequestException:
                event("oracle_failed", resource=resource, attempt=attempt + 1, reason="request_error")
                raise APIError("oracle_request", "Oracle request failed", 502,
                               outcome="unknown" if body is not None else None) from None
            status = response.status_code
            event("oracle_response", method=method, resource=resource, attempt=attempt + 1, status=status,
                  elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
            if 200 <= status < 300:
                return response
            retry_after = response.headers.get("Retry-After")
            response.close()
            if method == "GET" and (status == 429 or 500 <= status <= 599) and attempt + 1 < attempts:
                self._delay(attempt, retry_after)
                continue
            raise APIError("oracle_http", "Oracle rejected the request", 502, oracle_status=status,
                           outcome=("unknown" if status >= 500 else "rejected") if body is not None else None)

    def _chunks(self, response, maximum):
        try:
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isascii() or not declared.isdigit() or int(declared) > maximum):
                raise APIError("size_limit", "Oracle content has an invalid or excessive size", 502)
            count = 0
            for chunk in response.iter_content(chunk_size=65536):
                count += len(chunk)
                if count > maximum:
                    raise APIError("size_limit", "Oracle content exceeded its byte limit", 502)
                yield chunk
            if declared is not None and response.headers.get("Content-Encoding", "identity") == "identity" and count != int(declared):
                raise APIError("incomplete_content", "Oracle content transfer was incomplete", 502)
        except requests.RequestException:
            raise APIError("oracle_stream", "Oracle content transfer failed", 502) from None

    def _json(self, path, params=None):
        response = self._send(path, params)
        try:
            raw = b"".join(self._chunks(response, self.settings.max_json_bytes))
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeDecodeError):
            raise APIError("invalid_json", "Oracle returned invalid JSON", 502) from None
        finally:
            response.close()

    # Entity paths are relative to Settings.resource_base (/hcmRestApi/resources/{version}/).
    # GET one Oracle item; successful reads use the shared cache unless fresh=True.
    def item(self, path, params=None, *, fresh=False):
        def load():
            return self._json(path, {"onlyData": "false", **(params or {})})
        if fresh:
            return load()
        value, hit = self.cache.load(("item", path, json.dumps(params or {}, sort_keys=True)), load)
        event("oracle_item", cache_hit=hit)
        return value

    def _next_offset(self, payload, path, params, offset, count):
        links = payload.get("links", [])
        if not isinstance(links, list):
            raise APIError("invalid_pagination", "Oracle returned invalid pagination links", 502)
        next_links = [link for link in links if isinstance(link, dict) and link.get("rel") == "next"]
        if len(next_links) > 1:
            raise APIError("invalid_pagination", "Oracle returned ambiguous next-page links", 502)
        if not next_links:
            return offset + count
        expected = self.url(path)
        parsed = urlsplit(self.url(next_links[0].get("href"), relative_to=expected))
        if parsed.path != urlsplit(expected).path:
            raise APIError("unsafe_pagination", "Oracle pagination changed the resource", 502)
        query = parse_qs(parsed.query, keep_blank_values=True)
        # Preserve original finder, fields, and filters; accept only matching scope.
        for key, values in query.items():
            if len(values) != 1 or (key not in ("offset", "limit") and str(params.get(key)) != values[0]):
                raise APIError("unsafe_pagination", "Oracle pagination changed the query scope", 502)
        if "limit" in query and query["limit"][0] != str(params["limit"]):
            raise APIError("unsafe_pagination", "Oracle pagination changed the page size", 502)
        value = query.get("offset", [str(offset + count)])[0]
        if not value.isascii() or not value.isdigit() or int(value) != offset + count:
            raise APIError("invalid_pagination", "Oracle pagination did not advance by the returned row count", 502)
        return int(value)

    # GET Oracle collection pages, preserving q/finder/fields on every page and enforcing retrieval bounds.
    def collection(self, path, params=None, *, limit=None, offset=0, retrieve_all=True, fresh=False):
        limit = limit or self.settings.page_size
        if isinstance(limit, bool) or not 1 <= limit <= 500 or offset < 0:
            raise invalid("Invalid pagination parameters")
        scope = {"onlyData": "false", **(params or {}), "limit": limit}

        def load():
            data, errors, pages, fingerprints = [], [], 0, set()
            result_bytes = 0
            current, more, next_offset, total = offset, True, offset, None
            while True:
                try:
                    payload = self._json(path, {**scope, "offset": current})
                    items = payload.get("items")
                    if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                        raise APIError("invalid_collection", "Oracle returned an invalid collection", 502)
                    more = payload.get("hasMore")
                    if "offset" in payload and payload["offset"] != current:
                        raise APIError("invalid_pagination", "Oracle returned an unexpected page offset", 502)
                    serialized = json.dumps(items, sort_keys=True).encode()
                    fingerprint = hashlib.sha256(serialized).digest()
                    if items and fingerprint in fingerprints:
                        raise APIError("pagination_loop", "Oracle repeated a previously returned page", 502)
                    fingerprints.add(fingerprint)
                    remaining = self.settings.max_items - len(data)
                    selected = items[:remaining]
                    if result_bytes + len(serialized) > self.settings.max_result_bytes:
                        # Retain whole rows up to the aggregate bound; never truncate fields.
                        selected = []
                        for row in items[:remaining]:
                            size = len(json.dumps(row, sort_keys=True).encode()) + 2
                            if result_bytes + size > self.settings.max_result_bytes:
                                break
                            selected.append(row)
                            result_bytes += size
                    else:
                        result_bytes += len(serialized)
                    data.extend(selected)
                    pages += 1
                    total = payload.get("totalResults", total)
                    next_offset = current + len(selected)
                    if len(selected) < len(items):
                        more = True
                        raise APIError("result_limit", "Collection reached the configured item or aggregate byte bound", 502)
                    if not isinstance(more, bool):
                        raise APIError("invalid_pagination", "Oracle omitted a valid hasMore marker", 502)
                    if more:
                        if not items:
                            raise APIError("pagination_stalled", "Oracle reported more rows after an empty page", 502)
                        next_offset = self._next_offset(payload, path, scope, current, len(items))
                    if not more or not retrieve_all:
                        break
                    if pages >= self.settings.max_pages or len(data) >= self.settings.max_items:
                        raise APIError("pagination_limit", "Collection reached the configured retrieval bound", 502)
                    current = next_offset
                except APIError as exc:
                    errors.append(exc.public())
                    more = True
                    # If a page failed, resume at that page, preserving successful rows.
                    next_offset = max(next_offset, current)
                    break
            return envelope(
                data, path, errors=errors, pages=pages, returned_count=len(data),
                retrieval_status=("partial" if data else "unavailable") if errors else "retrieved",
                pagination_complete=not more and not errors, has_more=more,
                next_offset=next_offset if more else None,
                offset=offset, limit=limit, retrieve_all=retrieve_all,
                estimated_total=total if isinstance(total, int) and not isinstance(total, bool) else None,
            )

        key = ("collection", path, json.dumps(scope, sort_keys=True), offset, retrieve_all)
        if fresh:
            value, hit = load(), False
        else:
            value, hit = self.cache.load(key, load, cacheable=lambda value: not value["metadata"]["errors"])
        event("oracle_collection", cache_hit=hit, pages=value["metadata"]["pages"],
              returned_count=value["metadata"]["returned_count"], retrieval_status=value["metadata"]["retrieval_status"])
        value["metadata"]["cache_hit"] = hit
        return value

    def require_complete(self, result):
        if not result["metadata"]["pagination_complete"] or result["metadata"]["errors"]:
            raise APIError("incomplete_lookup", "A complete Oracle lookup is required to verify identity", 502)
        return result["data"]

    def text(self, path):
        response = self._send(path, binary=True)
        try:
            media = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].lower()
            if media not in ("application/octet-stream", "text/plain", "text/html"):
                raise APIError("invalid_content", "Oracle returned an unsupported description type", 502)
            return b"".join(self._chunks(response, self.settings.max_text_bytes)).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise APIError("invalid_encoding", "Oracle description is not UTF-8 text", 502) from None
        finally:
            response.close()

    def copy_binary(self, path, destination, maximum):
        """Destination is a private, exclusive temporary file owned by the service."""
        response = self._send(path, binary=True)
        try:
            media = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].lower()
            if media in ("application/json", "text/html"):
                raise APIError("invalid_content", "Oracle did not return binary resume content", 502)
            count = 0
            for chunk in self._chunks(response, maximum):
                destination.write(chunk)
                count += len(chunk)
            return count
        finally:
            response.close()

    def move(self, key, body):
        """Never retry a write. Clear cached reads even when its outcome is uncertain."""
        try:
            # POST /recruitingJobApplications/{key}/action/move is the only upstream write operation.
            response = self._send(f"recruitingJobApplications/{identifier(key)}/action/move", body=body)
            response.close()  # Oracle does not document a required response schema.
        finally:
            self.cache.clear()
