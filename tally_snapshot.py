"""Read the numeric Hermes response tally without exposing response content."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_CURRENT_FIELDS = {
    "input_tokens": ("Input tokens",),
    "output_tokens": ("Output tokens",),
    "cache_read_tokens": ("Cache-read tokens",),
    "cache_write_tokens": ("Cache-write tokens",),
    "reasoning_tokens": ("Reasoning tokens",),
    "total_tokens": ("Canonical total tokens", "Total tokens", "Tokens Used"),
    "expanded_activity_tokens": ("Expanded activity tokens",),
    "calls": ("Calls",),
    "thinking_seconds": ("Thinking time",),
    "working_seconds": ("Working time",),
    "words": ("Word count",),
    "characters": ("Character count",),
    "coding_characters": ("Coding character count",),
}

_RUNNING_FIELDS = {
    "input_tokens": ("Input Tokens Running tally",),
    "output_tokens": ("Output Tokens Running tally",),
    "cache_read_tokens": ("Cache-read Tokens Running tally",),
    "cache_write_tokens": ("Cache-write Tokens Running tally",),
    "reasoning_tokens": ("Reasoning Tokens Running tally",),
    "total_tokens": ("Canonical Total Tokens Running tally",),
    "expanded_activity_tokens": (
        "Expanded Activity Tokens Running tally",
        "Total Tokens Running tally",
        "Tokens Used Running tally",
    ),
    "calls": ("Calls Running tally",),
    "thinking_seconds": ("Thinking Time Running tally",),
    "working_seconds": ("Working Time Running tally",),
    "words": ("Words Running tally",),
    "characters": ("Character Running tally",),
    "coding_characters": ("Coding Character Running tally",),
}

_UNAVAILABLE = {"", "n/a", "unavailable", "not available", "none"}
_NUMBER = re.compile(r"^([+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?)\b")


def _empty_scope() -> dict[str, int | float | None]:
    return {key: None for key in _CURRENT_FIELDS}


def empty_tally_snapshot() -> dict[str, Any]:
    return {"updated": None, "current": _empty_scope(), "running": _empty_scope(),
            "working_time_missing_responses": None}


_CURRENT_RESPONSE_HEADING = re.compile(r"(?mi)^[ \t]*##[ \t]+Current response[ \t]*$")


def _latest_block(text: str) -> str:
    matches = list(_CURRENT_RESPONSE_HEADING.finditer(text))
    if matches:
        start = matches[-1].end()
        next_heading = re.search(r"(?m)^[ \t]*##\s+", text[start:])
        return text[start : start + next_heading.start()] if next_heading else text[start:]
    blocks = re.split(r"(?m)^##\s+", text)
    return blocks[-1] if blocks else ""


def _field_map(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        fields[label.strip()] = value.strip()
    return fields


def _number(value: str | None) -> int | float | None:
    if value is None or value.strip().casefold() in _UNAVAILABLE:
        return None
    match = _NUMBER.match(value.strip())
    if match is None:
        return None
    raw = match.group(1).replace(",", "")
    number = float(raw) if "." in raw else int(raw)
    return number if number >= 0 else None


def _first_number(fields: dict[str, str], labels: tuple[str, ...]) -> int | float | None:
    for label in labels:
        if label in fields:
            return _number(fields[label])
    return None


def parse_tally_snapshot(text: str) -> dict[str, Any]:
    """Return only labeled numeric tally fields from the latest response block."""
    fields = _field_map(_latest_block(text))
    snapshot = empty_tally_snapshot()
    snapshot["updated"] = fields.get("Updated") or None
    snapshot["current"] = {
        key: _first_number(fields, labels) for key, labels in _CURRENT_FIELDS.items()
    }
    snapshot["running"] = {
        key: _first_number(fields, labels) for key, labels in _RUNNING_FIELDS.items()
    }
    missing = _number(fields.get("Working Time Unavailable Responses"))
    snapshot["working_time_missing_responses"] = missing if isinstance(missing, int) else None
    return snapshot


def default_tally_path() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "tally.md"
    except ImportError:
        home = os.environ.get("HERMES_HOME", "").strip()
        return (Path(home) if home else Path.home() / ".hermes") / "tally.md"


def read_tally_snapshot(path: Path | None = None) -> dict[str, Any]:
    """Read a tally file safely; unavailable storage becomes an empty snapshot."""
    try:
        return parse_tally_snapshot((path or default_tally_path()).read_text(encoding="utf-8"))
    except OSError:
        return empty_tally_snapshot()
