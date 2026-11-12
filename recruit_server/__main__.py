"""Launch locally; configuration and credentials come only from the environment."""
import sys

import uvicorn

from recruit_server.http.api import create_app
from recruit_server.core.config import Settings
from recruit_server.core.observability import configure_logging, event


def main():
    configure_logging()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print("Configuration error: " + str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    # Access logs contain request paths/person numbers. Disable them by default.
    event("server_starting", host="127.0.0.1", port=settings.port, cache_ttl_seconds=settings.cache_ttl)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port,
                access_log=False, log_level="critical", proxy_headers=False)


if __name__ == "__main__":
    main()
