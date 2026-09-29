import logging
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
_PATTERNS = (
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)([?&](?:access_token|token|verifier|sf_verifier|api_key|key)=)"
        r"[^&#\s]+"
    ),
    re.compile(r"(?i)\b(sk-ant-[A-Za-z0-9_-]+)"),
    re.compile(r"(?i)\b(sk-(?:proj-)?[A-Za-z0-9_-]{16,})"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"),
    re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)"),
)
_SECRET_KEYS = {
    "authorization",
    "token",
    "access_token",
    "api_key",
    "anthropic_api_key",
    "openai_api_key",
    "deepseek_api_key",
    "verifier",
    "sf_verifier",
    "ics_key",
}


def redact_text(value: str) -> str:
    result = value
    for pattern in _PATTERNS:
        if pattern.pattern.startswith("(?i)\\b(Bearer"):
            result = pattern.sub(r"\1" + REDACTED, result)
        elif pattern.pattern.startswith("(?i)([?&]"):
            result = pattern.sub(r"\1" + REDACTED, result)
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if str(key).lower() in _SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            record.args = redact(record.args)
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
