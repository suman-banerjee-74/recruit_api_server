"""Read settings exclusively from environment variables, with strict bounds."""
import math
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    base_url: str
    fusion_username: str = field(repr=False)
    fusion_password: str = field(repr=False)
    version: str = "11.13.18.05"
    port: int = 8010
    timeout: float = 40
    max_retries: int = 2
    max_backoff: float = 10
    max_pages: int = 1000
    max_items: int = 100000
    page_size: int = 200
    max_json_bytes: int = 10 * 1024 * 1024
    max_result_bytes: int = 50 * 1024 * 1024
    max_resume_bytes: int = 20 * 1024 * 1024
    max_archive_bytes: int = 100 * 1024 * 1024
    max_text_bytes: int = 5 * 1024 * 1024
    cache_ttl: float = 60
    cache_entries: int = 256
    cache_bytes: int = 20 * 1024 * 1024
    enable_updates: bool = False

    def __post_init__(self):
        try:
            parsed = urlsplit(self.base_url)
            valid = (parsed.scheme == "https" and parsed.hostname and parsed.port != 0
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and parsed.path in ("", "/")
                     and "\\" not in self.base_url
                     and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.base_url))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("FUSION_BASE_URL must be an HTTPS origin without a path or credentials")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if not re.fullmatch(r"\d+(?:\.\d+)*", self.version):
            raise ValueError("FUSION_API_VERSION must be a dotted numeric version")
        for name in ("fusion_username", "fusion_password"):
            value = getattr(self, name)
            if not value or any(c in value for c in "\r\n"):
                raise ValueError("FUSION_USERNAME and FUSION_PASSWORD are required")
        if ":" in self.fusion_username:
            raise ValueError("Basic authentication usernames cannot contain a colon")
        for name, lower, upper in (
            ("port", 1, 65535), ("timeout", 0.1, 300), ("max_retries", 0, 5),
            ("max_backoff", 0, 60), ("max_pages", 1, 10000),
            ("max_items", 1, 1000000), ("page_size", 1, 500),
            ("max_json_bytes", 1024, 100 * 1024 * 1024),
            ("max_result_bytes", 1, 500 * 1024 * 1024),
            ("max_resume_bytes", 1, 100 * 1024 * 1024),
            ("max_archive_bytes", 1, 1024 * 1024 * 1024),
            ("max_text_bytes", 1, 20 * 1024 * 1024),
            ("cache_ttl", 0, 3600), ("cache_entries", 1, 10000),
            ("cache_bytes", 1, 500 * 1024 * 1024),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("Invalid numeric server configuration: " + name)

    @property
    def resource_base(self):
        return f"{self.base_url}/hcmRestApi/resources/{self.version}/"

    @classmethod
    def from_env(cls):
        def number(name, default, kind=int):
            try:
                return kind(os.environ.get(name, str(default)))
            except ValueError:
                raise ValueError("Invalid numeric environment variable: " + name) from None

        enabled = os.environ.get("FUSION_ENABLE_JOURNEY_UPDATES", "false").lower()
        if enabled not in ("true", "false"):
            raise ValueError("FUSION_ENABLE_JOURNEY_UPDATES must be true or false")
        return cls(
            base_url=os.environ.get("FUSION_BASE_URL", ""),
            fusion_username=os.environ.get("FUSION_USERNAME", ""),
            fusion_password=os.environ.get("FUSION_PASSWORD", ""),
            version=os.environ.get("FUSION_API_VERSION", "11.13.18.05"),
            port=number("RECRUIT_API_PORT", 8010),
            timeout=number("FUSION_TIMEOUT", 40, float),
            max_retries=number("FUSION_MAX_RETRIES", 2),
            max_backoff=number("FUSION_MAX_BACKOFF", 10, float),
            max_pages=number("FUSION_MAX_PAGES", 1000),
            max_items=number("FUSION_MAX_ITEMS", 100000),
            page_size=number("FUSION_PAGE_SIZE", 200),
            max_json_bytes=number("FUSION_MAX_JSON_BYTES", 10485760),
            max_result_bytes=number("FUSION_MAX_RESULT_BYTES", 52428800),
            max_resume_bytes=number("FUSION_MAX_RESUME_BYTES", 20971520),
            max_archive_bytes=number("FUSION_MAX_ARCHIVE_BYTES", 104857600),
            max_text_bytes=number("FUSION_MAX_TEXT_BYTES", 5242880),
            cache_ttl=number("RECRUIT_CACHE_TTL", 60, float),
            cache_entries=number("RECRUIT_CACHE_ENTRIES", 256),
            cache_bytes=number("RECRUIT_CACHE_BYTES", 20971520),
            enable_updates=enabled == "true",
        )
