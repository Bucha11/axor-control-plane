"""Launch-day visibility (launch-readiness §6): structured logs + optional Sentry.

- AXOR_LOG_JSON=1 → one JSON object per log line (machine-shippable to any
  collector; no agent required).
- SENTRY_DSN set AND sentry-sdk installed → errors ship to Sentry. The import
  is optional on purpose: no new hard dependency, and absence is logged, not
  fatal. Unhandled route exceptions are always logged with structure either way.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            out["exc_type"] = record.exc_info[0].__name__
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_observability() -> None:
    """Idempotent; called from the app factory."""
    if os.environ.get("AXOR_LOG_JSON", "") == "1":
        root = logging.getLogger()
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.handlers = [handler]
        root.setLevel(logging.INFO)

    dsn = os.environ.get("SENTRY_DSN", "")
    if dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0)
            logging.getLogger("axor.backend").info("sentry enabled")
        except ImportError:
            logging.getLogger("axor.backend").warning(
                "SENTRY_DSN set but sentry-sdk not installed — "
                "pip install sentry-sdk to ship errors"
            )
