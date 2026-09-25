import json
import logging
import time
import uuid


def request_id() -> str:
    return "req_" + uuid.uuid4().hex


class RequestTimer:
    def __init__(self) -> None:
        self.started = time.perf_counter()

    @property
    def latency_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 2)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        if hasattr(record, "trace_data"):
            payload.update(record.trace_data)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx's informational request logs include full URLs and query strings.
    # Those strings can contain private user questions or source filters.
    # Keep request diagnostics in our structured trace instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
