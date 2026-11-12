"""Operational events with generated correlation IDs and no request/response data."""
import logging
from contextvars import ContextVar


request_id = ContextVar("request_id", default="-")
logger = logging.getLogger("recruit_server")


def configure_logging():
    """Configure only this application's logger; keep third-party logging unchanged."""
    if not any(getattr(handler, "recruit_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.recruit_handler = True
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def event(name, **fields):
    """Callers supply fixed labels and operational counters, never arbitrary inputs."""
    logger.info("event=%s request_id=%s %s", name, request_id.get(),
                " ".join(f"{key}={value}" for key, value in fields.items()))
