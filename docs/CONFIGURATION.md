# Environment configuration

All configuration is read from environment variables at startup. `.env.example`
is a template for this standalone folder; the application loads no files implicitly.
Restart after changing settings. Oracle Basic authentication is used even if an
unrelated `FUSION_TOKEN` exists in the process environment; token auth is not enabled.

| Variable | Default | Purpose |
|---|---|---|
| FUSION_BASE_URL | Required | HTTPS Oracle instance origin, without resource path/query/credentials |
| FUSION_USERNAME | Required | Shared Oracle and server Basic username |
| FUSION_PASSWORD | Required | Shared Oracle and server Basic password |
| FUSION_API_VERSION | 11.13.18.05 | Dotted Oracle REST version |
| RECRUIT_API_PORT | 8010 | Loopback listener port, 1–65535 |
| FUSION_TIMEOUT | 40 | Connect/read inactivity timeout in seconds, 0.1–300 |
| FUSION_MAX_RETRIES | 2 | Additional GET attempts, 0–5; POST always has zero retries |
| FUSION_MAX_BACKOFF | 10 | Maximum retry wait in seconds, 0–60; numeric/date Retry-After honored within bound |
| FUSION_PAGE_SIZE | 200 | Default collection page size, 1–500 |
| FUSION_MAX_PAGES | 1000 | Maximum pages per collection, 1–10000 |
| FUSION_MAX_ITEMS | 100000 | Maximum rows per collection, 1–1000000 |
| FUSION_MAX_JSON_BYTES | 10485760 | Maximum decoded JSON bytes per Oracle response |
| FUSION_MAX_RESULT_BYTES | 52428800 | Approximate serialized row bytes per collection before enrichment |
| FUSION_MAX_RESUME_BYTES | 20971520 | Maximum bytes per downloaded resume |
| FUSION_MAX_ARCHIVE_BYTES | 104857600 | Maximum cumulative uncompressed resume bytes and final ZIP bytes |
| FUSION_MAX_TEXT_BYTES | 5242880 | Maximum UTF-8 bytes per description enclosure |
| RECRUIT_CACHE_TTL | 60 | Shared cache lifetime in seconds, 0–3600; 0 disables it |
| RECRUIT_CACHE_ENTRIES | 256 | Maximum process cache entries, 1–10000 |
| RECRUIT_CACHE_BYTES | 20971520 | Approximate serialized process cache payload byte bound |
| FUSION_ENABLE_JOURNEY_UPDATES | false | Enable the sole Oracle write action; exactly true/false |

Row/JSON limits protect individual source collections; optional per-record children,
descriptions, and profiles add data beyond the parent collection's byte bound.
Request duration includes potentially many pages, retries, and enrichment calls.
The inactivity timeout is not a global request deadline. Start with default bounds;
use one-page requests or omit enrichment for lower latency and memory use.

TLS verification is mandatory. Redirects are disabled. The transport ignores
ambient proxy/netrc configuration so credentials cannot silently change or be
routed through an unintended proxy. Private deployments needing a proxy/custom
trust configuration must implement and review that explicitly; do not disable TLS.

The launcher fixes the host to loopback, uses one process, disables request access
logs, and ignores forwarded headers. Remote deployment requires a private HTTPS
reverse proxy with its own authentication/authorization, request limits, and
sanitized logging. This server has no per-caller Oracle impersonation, distributed
cache invalidation, or durable audit store. Server-side Basic credentials should
be rotated through your deployment environment.
