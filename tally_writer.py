"""Content-free local tally finalization and recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .tally_snapshot import parse_tally_snapshot
except ImportError:
    from tally_snapshot import parse_tally_snapshot

_WORD = re.compile(r"\S+")
_FENCED_CODE = re.compile(
    r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t]*\1[ \t]*$"
)
_INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_HERMES_USAGE_FOOTER = re.compile(
    r"(?is)\n{0,2}---[ \t]*\n(?:[ \t]*\n)*\*\*Hermes usage:\*\*[\s\S]*\Z"
)
_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "expanded_activity_tokens",
    "calls",
    "thinking_seconds",
    "working_seconds",
    "words",
    "characters",
    "coding_characters",
)
_TEXT_FIELDS = ("words", "characters", "coding_characters", "working_seconds")
_REQUEST_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
_PROVIDER_RUNNING_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "expanded_activity_tokens",
    "calls",
    "thinking_seconds",
)
_CURRENT_LABELS = {
    "input_tokens": "Input tokens",
    "output_tokens": "Output tokens",
    "cache_read_tokens": "Cache-read tokens",
    "cache_write_tokens": "Cache-write tokens",
    "reasoning_tokens": "Reasoning tokens",
    "total_tokens": "Canonical total tokens",
    "expanded_activity_tokens": "Expanded activity tokens",
    "calls": "Calls",
    "thinking_seconds": "Thinking time",
    "working_seconds": "Working time",
    "words": "Word count",
    "characters": "Character count",
    "coding_characters": "Coding character count",
}
_RUNNING_LABELS = {
    "input_tokens": "Input Tokens Running tally",
    "output_tokens": "Output Tokens Running tally",
    "cache_read_tokens": "Cache-read Tokens Running tally",
    "cache_write_tokens": "Cache-write Tokens Running tally",
    "reasoning_tokens": "Reasoning Tokens Running tally",
    "total_tokens": "Canonical Total Tokens Running tally",
    "expanded_activity_tokens": "Expanded Activity Tokens Running tally",
    "calls": "Calls Running tally",
    "thinking_seconds": "Thinking Time Running tally",
    "working_seconds": "Working Time Running tally",
    "words": "Words Running tally",
    "characters": "Character Running tally",
    "coding_characters": "Coding Character Running tally",
}
_COUNT_LABELS = {
    "thinking_unavailable": "Thinking Time Unavailable Responses",
    "working_unavailable": "Working Time Unavailable Responses",
}
_WORKING_POLICY_NOTE = (
    "## Working-time accounting\n"
    "Working Time Running tally is the confirmed baseline plus measured agent-turn wall-clock durations. "
    "When Working Time Unavailable Responses is nonzero, it is an incomplete observed cumulative total; "
    "unmeasured durations are omitted, not estimated."
)


def measure_response_text(response: str) -> dict[str, int]:
    """Count response text in memory and return only numeric accounting fields."""
    response = _HERMES_USAGE_FOOTER.sub("", response)
    fenced_characters = sum(len(match.group(2)) for match in _FENCED_CODE.finditer(response))
    without_fences = _FENCED_CODE.sub("", response)
    inline_characters = sum(len(match.group(1)) for match in _INLINE_CODE.finditer(without_fences))
    return {
        "words": len(_WORD.findall(response)),
        "characters": len(response),
        "coding_characters": fenced_characters + inline_characters,
    }


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0 or int(value) != value:
        return None
    return int(value)


def _safe_timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _number_text(value: int | float | None) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _derive_token_totals(
    metrics: dict[str, int | float | None],
) -> dict[str, int | float | None]:
    """Set explicitly scoped totals only when every required component is observed."""
    canonical_components = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    )
    canonical_values = [metrics.get(field) for field in canonical_components]
    if all(value is not None for value in canonical_values):
        metrics["total_tokens"] = int(sum(int(value) for value in canonical_values))
    expanded_values = [*canonical_values, metrics.get("reasoning_tokens")]
    if all(value is not None for value in expanded_values):
        metrics["expanded_activity_tokens"] = int(sum(int(value) for value in expanded_values))
    return metrics


_CURRENT_RESPONSE_HEADING = re.compile(r"(?mi)^[ \t]*##[ \t]+Current response[ \t]*$")
_PROVIDER_BASELINE_BLOCK = re.compile(
    r"(?ms)^[ \t]*##[ \t]+Provider running-total baseline[ \t]*\n.*?(?=^[ \t]*##[ \t]+|\Z)"
)


def _latest_fields(text: str) -> dict[str, str]:
    matches = list(_CURRENT_RESPONSE_HEADING.finditer(text))
    if matches:
        start = matches[-1].end()
        next_heading = re.search(r"(?m)^[ \t]*##\s+", text[start:])
        block = text[start : start + next_heading.start()] if next_heading else text[start:]
    else:
        block = re.split(r"(?m)^##\s+", text)[-1]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            label, value = line.split(":", 1)
            fields[label.strip()] = value.strip()
    return fields


def _nonnegative_int(value: str | None) -> int:
    try:
        return max(0, int(value or "0"))
    except (TypeError, ValueError):
        return 0


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(self: "TallyWriter", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class TallyWriter:
    """Durably projects numeric final-response accounting to a local tally file."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        tally_path: str | os.PathLike[str],
        *,
        replace: Callable[[str, str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.tally_path = Path(tally_path)
        self._replace = replace or os.replace
        self._lock = threading.RLock()

    @_locked
    def migrate(self) -> None:
        self.connection.execute("PRAGMA busy_timeout = 5000")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS tally_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS turn_starts (
                    turn_ref TEXT PRIMARY KEY,
                    started_at REAL NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS staged_requests (
                    event_ref TEXT PRIMARY KEY,
                    turn_ref TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    cache_write_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    api_duration REAL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completed_turns (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_ref TEXT UNIQUE NOT NULL,
                    commit_ref TEXT UNIQUE NOT NULL,
                    completed_at REAL NOT NULL,
                    words INTEGER NOT NULL,
                    characters INTEGER NOT NULL,
                    coding_characters INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'applied'))
                )
                """
            )
            if self._meta("schema_version") != "2":
                self._set_meta("schema_version", "2")
            if self._meta("identity_key") is None:
                self._set_meta("identity_key", secrets.token_hex(32))
            if self._meta("baseline") is None:
                self._set_meta("baseline", json.dumps(self._read_baseline(), sort_keys=True))
            if self._meta("baseline_sequence") is None:
                self._set_meta("baseline_sequence", "0")
            if self._meta("observed_sum_fields") is None:
                self._set_meta("observed_sum_fields", "[]")
            observed_fields = self._observed_sum_fields()
            if "working_seconds" not in observed_fields:
                self._set_meta("observed_sum_fields", json.dumps(sorted(observed_fields | {"working_seconds"})))
                # Reproject already-applied history once; preserve the saved baseline and all measurements.
                self.connection.execute(
                    "UPDATE completed_turns SET status = 'pending' "
                    "WHERE sequence = (SELECT MAX(sequence) FROM completed_turns)"
                )
        self.recover()

    @_locked
    def record_turn_started(self, turn_id: object, *, started_at: object = None) -> bool:
        turn_ref = self._turn_ref(turn_id)
        started = _safe_timestamp(time.time() if started_at is None else started_at)
        if turn_ref is None or started is None:
            return False
        with self.connection:
            result = self.connection.execute(
                "INSERT OR IGNORE INTO turn_starts (turn_ref, started_at) VALUES (?, ?)",
                (turn_ref, started),
            )
        return result.rowcount == 1

    @_locked
    def stage_request(
        self,
        *,
        api_request_id: object,
        turn_id: object,
        usage: object,
        api_duration: object,
    ) -> bool:
        event_ref = self._event_ref(api_request_id)
        turn_ref = self._turn_ref(turn_id)
        if event_ref is None or turn_ref is None:
            return False
        values = usage if isinstance(usage, Mapping) else {}
        metrics = [_positive_int(values.get(field)) for field in _REQUEST_FIELDS]
        duration = _safe_timestamp(api_duration)
        if duration is not None and duration < 0:
            duration = None
        with self.connection:
            result = self.connection.execute(
                """
                INSERT OR IGNORE INTO staged_requests (
                    event_ref, turn_ref, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens, reasoning_tokens, api_duration
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_ref, turn_ref, *metrics, duration),
            )
            late_turn = False
            if result.rowcount == 1:
                late_turn = self.connection.execute(
                    "UPDATE completed_turns SET status = 'pending' WHERE turn_ref = ?", (turn_ref,)
                ).rowcount == 1
        if result.rowcount != 1:
            return False
        if late_turn:
            try:
                self._materialize_serialized()
            except OSError:
                # The newly staged request remains in the durable pending projection.
                pass
        return True

    @_locked
    def complete_turn(
        self, turn_id: object, response: object, *, ended_at: object = None
    ) -> bool:
        turn_ref = self._turn_ref(turn_id)
        completed_at = _safe_timestamp(time.time() if ended_at is None else ended_at)
        if turn_ref is None or completed_at is None or not isinstance(response, str):
            return False
        measured = measure_response_text(response)
        commit_ref = self._commit_ref(turn_ref)
        with self.connection:
            result = self.connection.execute(
                """
                INSERT OR IGNORE INTO completed_turns (
                    turn_ref, commit_ref, completed_at, words, characters, coding_characters, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    turn_ref,
                    commit_ref,
                    completed_at,
                    measured["words"],
                    measured["characters"],
                    measured["coding_characters"],
                ),
            )
        if result.rowcount != 1:
            self.recover()
            return False
        try:
            self._materialize_serialized()
        except OSError:
            # The durable pending row will be replayed from SQLite after restart.
            return False
        return True

    @_locked
    def recover(self) -> None:
        pending = self.connection.execute(
            "SELECT 1 FROM completed_turns WHERE status = 'pending' LIMIT 1"
        ).fetchone()
        if pending is not None or self._meta("provider_reseed_pending") == "1":
            self._materialize_serialized()

    @_locked
    def adopt_running_baseline_from_tally(self) -> bool:
        """Adopt the latest canonical tally's running values before future additions.

        Read the file and its matching sequence under the shared SQLite write lock.
        Commit the adopted baseline as pending before the separately serialized projection,
        so a failed file replacement can be recovered without repeating the adoption.
        """
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.connection.execute(
                "SELECT 1 FROM completed_turns WHERE status = 'pending' LIMIT 1"
            ).fetchone() is not None or self._meta("provider_reseed_pending") == "1":
                # The tally file must represent every completed row before its Running column is adopted.
                return False
            rows = self._completed_rows()
            if not rows:
                return False
            try:
                tally_text = self.tally_path.read_text(encoding="utf-8")
            except OSError:
                return False
            snapshot = parse_tally_snapshot(tally_text)
            baseline = json.loads(self._meta("baseline") or "{}")
            previous_running = self._running_metrics(
                rows,
                baseline,
                self._baseline_sequence(),
                self._observed_sum_fields(),
            )
            next_running = dict(baseline.get("running", {}))
            for field in _FIELDS:
                next_running[field] = snapshot["running"].get(field, previous_running[field])
                if next_running[field] is None:
                    next_running[field] = previous_running[field]
            fields = _latest_fields(tally_text)
            next_baseline = {
                **baseline,
                "running": next_running,
                "thinking_unavailable": _nonnegative_int(
                    fields.get(_COUNT_LABELS["thinking_unavailable"])
                ),
                "working_unavailable": _nonnegative_int(
                    fields.get(_COUNT_LABELS["working_unavailable"])
                ),
            }
            self._set_meta("baseline", json.dumps(next_baseline, sort_keys=True))
            self._set_meta("baseline_sequence", str(int(rows[-1][0])))
            self._set_meta("observed_sum_fields", json.dumps((*_PROVIDER_RUNNING_FIELDS, "working_seconds")))
            self._set_meta("provider_reseeded_at", str(time.time()))
            self._set_meta("provider_reseed_pending", "1")
        try:
            self._materialize_serialized()
        except OSError:
            return False
        return True

    def _materialize_serialized(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._materialize()
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _materialize(self) -> None:
        rows = self.connection.execute(
            """
            SELECT sequence, turn_ref, commit_ref, completed_at, words, characters, coding_characters
            FROM completed_turns
            ORDER BY sequence
            """
        ).fetchall()
        if not rows:
            return
        baseline = json.loads(self._meta("baseline") or "{}")
        current_row = rows[-1]
        current = self._current_metrics(current_row)
        running = self._running_metrics(
            rows,
            baseline,
            self._baseline_sequence(),
            self._observed_sum_fields(),
        )
        contents = self._render(current_row, current, running, baseline)
        self._atomic_replace(contents)
        self._mark_pending_applied()

    def _mark_pending_applied(self) -> None:
        self.connection.execute("UPDATE completed_turns SET status = 'applied' WHERE status = 'pending'")
        if self._meta("provider_reseed_pending") == "1":
            self._set_meta("provider_reseed_pending", "0")

    def _current_metrics(self, row: tuple[Any, ...]) -> dict[str, int | float | None]:
        _, turn_ref, _, completed_at, words, characters, coding_characters = row
        current = self._request_metrics(turn_ref)
        started = self.connection.execute(
            "SELECT started_at FROM turn_starts WHERE turn_ref = ?", (turn_ref,)
        ).fetchone()
        current["working_seconds"] = None
        if started is not None:
            duration = float(completed_at) - float(started[0])
            if math.isfinite(duration) and duration >= 0:
                current["working_seconds"] = duration
        current["words"] = int(words)
        current["characters"] = int(characters)
        current["coding_characters"] = int(coding_characters)
        return current

    def _request_metrics(self, turn_ref: str) -> dict[str, int | float | None]:
        rows = self.connection.execute(
            """
            SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                   reasoning_tokens, api_duration
            FROM staged_requests WHERE turn_ref = ?
            """,
            (turn_ref,),
        ).fetchall()
        metrics = {field: None for field in _FIELDS}
        if not rows:
            return metrics
        metrics["calls"] = len(rows)
        for index, field in enumerate(_REQUEST_FIELDS):
            values = [row[index] for row in rows]
            if all(value is not None for value in values):
                metrics[field] = sum(int(value) for value in values)
        durations = [row[-1] for row in rows]
        if all(value is not None for value in durations):
            metrics["thinking_seconds"] = sum(float(value) for value in durations)
        return _derive_token_totals(metrics)

    def _running_metrics(
        self,
        rows: list[tuple[Any, ...]],
        baseline: dict[str, Any],
        baseline_sequence: int,
        observed_sum_fields: frozenset[str],
    ) -> dict[str, int | float | None]:
        running = {field: None for field in _FIELDS}
        post_baseline_rows = [row for row in rows if int(row[0]) > baseline_sequence]
        for field in _FIELDS:
            base = baseline.get("running", {}).get(field)
            if base is None:
                continue
            values = [self._current_metrics(row)[field] for row in post_baseline_rows]
            if field in observed_sum_fields:
                values = [value for value in values if value is not None]
            elif any(value is None for value in values):
                continue
            running[field] = float(base) + sum(float(value) for value in values)
            if float(running[field]).is_integer():
                running[field] = int(running[field])
        return _derive_token_totals(running)

    def _render(
        self,
        row: tuple[Any, ...],
        current: dict[str, int | float | None],
        running: dict[str, int | float | None],
        baseline: dict[str, Any],
    ) -> str:
        _, _, commit_ref, completed_at, *_ = row
        legacy, unmanaged_suffix = self._legacy_parts()
        updated = datetime.fromtimestamp(float(completed_at), timezone.utc).isoformat()
        post_baseline_rows = [
            item for item in self._completed_rows() if int(item[0]) > self._baseline_sequence()
        ]
        working_missing = _nonnegative_int(baseline.get("working_unavailable")) + sum(
            self._current_metrics(item)["working_seconds"] is None for item in post_baseline_rows
        )
        lines = [legacy, "", "## Current response", f"Updated: {updated}", f"Tally finalizer commit: {commit_ref}"]
        for field in _FIELDS:
            suffix = " seconds (observed provider API duration)" if field == "thinking_seconds" and current[field] is not None else ""
            if field == "working_seconds" and current[field] is not None:
                suffix = " seconds (agent turn wall-clock proxy)"
            lines.append(f"{_CURRENT_LABELS[field]}: {_number_text(current[field])}{suffix}")
        for field in _FIELDS:
            suffix = " seconds" if field in {"thinking_seconds", "working_seconds"} and running[field] is not None else ""
            if field == "working_seconds" and running[field] is not None and working_missing:
                suffix += " (incomplete; measured durations only)"
            lines.append(f"{_RUNNING_LABELS[field]}: {_number_text(running[field])}{suffix}")

        thinking_missing = _nonnegative_int(baseline.get("thinking_unavailable")) + sum(
            self._current_metrics(item)["thinking_seconds"] is None for item in post_baseline_rows
        )
        lines.append(f"{_COUNT_LABELS['thinking_unavailable']}: {thinking_missing}")
        lines.append(f"{_COUNT_LABELS['working_unavailable']}: {working_missing}")
        managed = "\n".join(lines).rstrip() + "\n"
        return managed + ("\n" + unmanaged_suffix if unmanaged_suffix else "")

    def _legacy_parts(self) -> tuple[str, str]:
        try:
            text = self.tally_path.read_text(encoding="utf-8")
        except OSError:
            return "# Hermes usage tally", ""
        matches = list(_CURRENT_RESPONSE_HEADING.finditer(text))
        unmanaged_suffix = ""
        if matches:
            marker = matches[-1]
            next_heading = re.search(r"(?m)^[ \t]*##\s+", text[marker.end():])
            if next_heading:
                unmanaged_suffix = text[marker.end() + next_heading.start():]
            legacy = text[:marker.start()].rstrip()
        else:
            legacy = text.rstrip()
        legacy = legacy or "# Hermes usage tally"
        legacy = _PROVIDER_BASELINE_BLOCK.sub("", legacy).rstrip()
        if "## Working-time accounting" not in legacy:
            legacy = f"{legacy}\n\n{_WORKING_POLICY_NOTE}"
        note = self._provider_baseline_note()
        return (f"{legacy}\n\n{note}".rstrip() if note else legacy), unmanaged_suffix

    def _atomic_replace(self, contents: str) -> None:
        self.tally_path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False, dir=self.tally_path.parent,
            prefix=f".{self.tally_path.name}.", suffix=".tmp",
        )
        try:
            with handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace(handle.name, str(self.tally_path))
        finally:
            try:
                Path(handle.name).unlink(missing_ok=True)
            except OSError:
                pass

    def _read_baseline(self) -> dict[str, Any]:
        existed = self.tally_path.is_file()
        try:
            text = self.tally_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        snapshot = parse_tally_snapshot(text)
        if not existed:
            # A fresh tally starts from a known empty local baseline.  Unsupported totals remain N/A.
            snapshot["running"] = {
                **snapshot["running"],
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "calls": 0,
                "thinking_seconds": 0,
                "working_seconds": 0,
                "words": 0,
                "characters": 0,
                "coding_characters": 0,
            }
        fields = _latest_fields(text)
        return {
            "running": snapshot["running"],
            "thinking_unavailable": _nonnegative_int(fields.get(_COUNT_LABELS["thinking_unavailable"])),
            "working_unavailable": _nonnegative_int(fields.get(_COUNT_LABELS["working_unavailable"])),
        }

    def _completed_rows(self) -> list[tuple[Any, ...]]:
        return self.connection.execute(
            """
            SELECT sequence, turn_ref, commit_ref, completed_at, words, characters, coding_characters
            FROM completed_turns
            ORDER BY sequence
            """
        ).fetchall()

    def _baseline_sequence(self) -> int:
        return _nonnegative_int(self._meta("baseline_sequence"))

    def _observed_sum_fields(self) -> frozenset[str]:
        try:
            fields = json.loads(self._meta("observed_sum_fields") or "[]")
        except json.JSONDecodeError:
            return frozenset()
        return frozenset(field for field in fields if field in _FIELDS)

    def _provider_baseline_note(self) -> str:
        try:
            updated_at = _safe_timestamp(float(self._meta("provider_reseeded_at") or "nan"))
        except ValueError:
            updated_at = None
        if updated_at is None:
            return ""
        updated = datetime.fromtimestamp(updated_at, timezone.utc).isoformat()
        return (
            "## Provider running-total baseline\n"
            f"Updated: {updated}\n"
            "Scope: copied from the latest Current response's running tally values; "
            "future observed values add from here without changing this baseline."
        )

    def _meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM tally_meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row is not None else None

    def _set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO tally_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _event_ref(self, api_request_id: object) -> str | None:
        if not isinstance(api_request_id, str) or not api_request_id:
            return None
        key = self._meta("identity_key")
        if key is None:
            return None
        return hmac.new(bytes.fromhex(key), f"event\0{api_request_id}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _turn_ref(self, turn_id: object) -> str | None:
        if not isinstance(turn_id, str) or not turn_id:
            return None
        key = self._meta("identity_key")
        if key is None:
            return None
        return hmac.new(bytes.fromhex(key), f"turn\0{turn_id}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _commit_ref(self, turn_ref: str) -> str:
        key = self._meta("identity_key")
        if key is None:
            raise RuntimeError("tally writer has not migrated")
        return hmac.new(bytes.fromhex(key), f"commit\0{turn_ref}".encode("utf-8"), hashlib.sha256).hexdigest()
