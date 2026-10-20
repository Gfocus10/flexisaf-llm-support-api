"""Structured JSON application log plus a CSV token/cost log."""
import csv
import json
import logging
import os
import threading
from datetime import datetime, timezone

LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_CSV_PATH = os.path.join(LOG_DIR, "usage_log.csv")
_CSV_FIELDS = [
    "timestamp", "request_id", "model", "used_fallback", "input_tokens",
    "output_tokens", "estimated_cost_usd", "latency_ms", "retry_count", "status",
]
_lock = threading.Lock()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def get_logger() -> logging.Logger:
    logger = logging.getLogger("support_api")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        for handler in (logging.StreamHandler(), logging.FileHandler(os.path.join(LOG_DIR, "app.log"))):
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
        logger.propagate = False
    return logger


def log_request(request_id, model, used_fallback, input_tokens, output_tokens,
                cost, latency_ms, retry_count, status) -> None:
    """One structured log line and one CSV row per request, success or failure."""
    get_logger().info("llm_request", extra={"fields": {
        "request_id": request_id, "model": model, "used_fallback": used_fallback,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "estimated_cost_usd": cost, "latency_ms": latency_ms,
        "retry_count": retry_count, "status": status,
    }})
    with _lock:
        new_file = not os.path.exists(_CSV_PATH)
        with open(_CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(_CSV_FIELDS)
            w.writerow([datetime.now(timezone.utc).isoformat(), request_id, model, used_fallback,
                        input_tokens, output_tokens, f"{cost:.6f}", latency_ms, retry_count, status])
