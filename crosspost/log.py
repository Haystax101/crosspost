"""Structured logging: one logfmt line per event, e.g.

    2026-10-26T16:15:02Z info published platform=youtube tiktok_id=123 post_id=abc

On GitHub Actions, warnings and errors are also emitted as workflow annotations so
they show up on the run summary page.
"""

from __future__ import annotations

import logging
import os
import sys
import time

_logger = logging.getLogger("crosspost")


def _fmt_value(value: object) -> str:
    text = str(value)
    if text == "" or any(c in text for c in ' "=\n'):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return text


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created))
        parts = [ts, record.levelname.lower(), record.getMessage()]
        for key, value in getattr(record, "fields", {}).items():
            parts.append(f"{key}={_fmt_value(value)}")
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if os.environ.get("GITHUB_ACTIONS") == "true" and record.levelno >= logging.WARNING:
            kind = "error" if record.levelno >= logging.ERROR else "warning"
            line += f"\n::{kind}::{record.getMessage()} " + " ".join(parts[3:])
        return line


def setup(verbose: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter())
    _logger.handlers[:] = [handler]
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    _logger.propagate = False


def _log(level: int, event: str, exc_info: bool, fields: dict) -> None:
    _logger.log(level, event, extra={"fields": fields}, exc_info=exc_info)


def debug(event: str, **fields: object) -> None:
    _log(logging.DEBUG, event, False, fields)


def info(event: str, **fields: object) -> None:
    _log(logging.INFO, event, False, fields)


def warning(event: str, **fields: object) -> None:
    _log(logging.WARNING, event, False, fields)


def error(event: str, exc_info: bool = False, **fields: object) -> None:
    _log(logging.ERROR, event, exc_info, fields)
