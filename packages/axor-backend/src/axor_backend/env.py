"""One way to read a setting out of the environment.

Three files had grown their own parsers, and all three failed the same way: a
value the parser did not understand became a default, or an exception that
quoted the value without naming the variable it came from.

- ``_flag`` accepted exactly ``"1"``. ``AXOR_WEBHOOK_BLOCK_PRIVATE=true`` was
  therefore False — an operator who believed they had turned the SSRF guard on
  had turned nothing on, silently, and the setting that fails this way is the
  one where failing means fail-OPEN.
- ``float(os.environ[...])`` in ``config``, ``limits`` and ``monitor`` raised
  ``could not convert string to float: 'thirty'``. In ``limits`` that is at
  import time, so a typo was a backend that would not start and a message that
  did not say which of five variables to look at.

So: an unset variable takes the default; a set one must parse, and a value this
module does not understand is a loud refusal naming the variable. Never a
quietly assumed default — that is the failure mode, not the remedy.
"""
from __future__ import annotations

import json
import os
from typing import Any

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


class EnvError(ValueError):
    """A configured value the process cannot use. Always names the variable."""


def _raw(name: str) -> str | None:
    return os.environ.get(name)


def flag(name: str, default: bool = False) -> bool:
    """A boolean setting. Accepts the usual spellings, case-insensitively."""
    raw = _raw(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise EnvError(
        f"{name}={raw!r} is not a yes/no value "
        f"(use one of {sorted(_TRUE)} or {sorted(_FALSE - {''})})"
    )


def number(
    name: str, default: float | None = None, *, minimum: float | None = None,
) -> float | None:
    """A float setting. An empty value means unset, like an absent variable."""
    raw = _raw(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise EnvError(f"{name}={raw!r} is not a number") from None
    if minimum is not None and value < minimum:
        raise EnvError(f"{name}={raw!r} must be >= {minimum}")
    return value


def integer(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _raw(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise EnvError(f"{name}={raw!r} is not a whole number") from None
    if minimum is not None and value < minimum:
        raise EnvError(f"{name}={raw!r} must be >= {minimum}")
    return value


def text(name: str, default: str = "") -> str:
    raw = _raw(name)
    return default if raw is None else raw


def json_object(name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """A JSON object setting — the operator keyring is one."""
    raw = _raw(name)
    if raw is None or not raw.strip():
        return {} if default is None else default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvError(f"{name} is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise EnvError(f"{name} must be a JSON object, got {type(parsed).__name__}")
    return parsed
