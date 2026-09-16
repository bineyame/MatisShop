"""Secret redaction for logs and the audit trail.

The audit trail must answer "what did we send?" without ever storing a
credential. Redaction is applied to every payload before persistence/logging.
"""

from __future__ import annotations

from typing import Any

SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "private_key",
    "privatekey",
    "credential",
    "signature",
    "pin",
    "card_number",
    "cvv",
)

MASK = "***REDACTED***"
_MAX_STRING = 4096


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a copy of ``value`` with sensitive keys masked.

    Also truncates very long strings so a rogue payload cannot bloat the
    audit tables.
    """
    if _depth > 12:
        return "***TRUNCATED_DEPTH***"
    if isinstance(value, dict):
        return {
            key: (MASK if _is_sensitive(str(key)) else redact(item, _depth=_depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + "...[truncated]"
    return value
