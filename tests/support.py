import json
from dataclasses import replace
from unittest.mock import Mock

from recruit_server.infrastructure.cache import Cache
from recruit_server.core.config import Settings
from recruit_server.infrastructure.oracle import OracleClient


BASE = "https://fusion.example/hcmRestApi/resources/11.13.18.05/"


def settings(**kwargs):
    return replace(Settings("https://fusion.example", "fusion-user", "fusion-secret",
                            max_backoff=0), **kwargs)


def page(items, more=False, **extra):
    return {"items": items, "hasMore": more, **extra}


def linked(collection, key, **fields):
    return {**fields, "links": [{"rel": "self", "href": BASE + collection + "/" + key}]}


def response(value=None, *, status=200, raw=None, headers=None):
    result = Mock()
    result.status_code = status
    result.headers = headers or {"Content-Type": "application/json"}
    content = raw if raw is not None else json.dumps(value).encode()
    result.iter_content.side_effect = lambda **kwargs: iter([content])
    return result


def client(payloads, *, config=None, cache=None):
    config = config or settings()
    session = Mock()
    session.headers = {}
    session.request.side_effect = payloads
    cache = cache or Cache(config.cache_ttl, config.cache_entries, config.cache_bytes)
    return OracleClient(config, cache, session), session
