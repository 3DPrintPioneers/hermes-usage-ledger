"""Local-only observed provider-request ledger for the usage tracker plugin."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
import threading
import time
from functools import wraps
from typing import Any, Mapping


def _observed_positive(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0 or int(value) != value:
        return None
    return int(value)


def _keyed_ref(digest: Any, key: bytes, scope: str) -> str | None:
    if not isinstance(digest, str) or not digest:
        return None
    return hmac.new(key, f"{scope}\0{digest}".encode("utf-8"), hashlib.sha256).hexdigest()


def _opaque_ref(value: Any, key: bytes, scope: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return _keyed_ref(digest, key, scope)


_IDENTITY_SCHEME = "hmac-sha256-of-sha256-v1"


_LABEL_RE = re.compile(r"[A-Za-z0-9._/:@+-]{1,128}")


def _label(value: Any) -> str | None:
    return value if isinstance(value, str) and _LABEL_RE.fullmatch(value) else None


def _locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class UsageLedger:
    """Stores opaque request/session identifiers and numeric usage fields only."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._lock = threading.RLock()

    @_locked
    def migrate(self) -> None:
        self.connection.execute("PRAGMA busy_timeout = 5000")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO ledger_meta (key, value) VALUES ('schema_version', '1')"
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS observed_requests (
                    event_id TEXT PRIMARY KEY,
                    session_ref TEXT,
                    turn_ref TEXT,
                    provider TEXT,
                    model TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    observed_at INTEGER NOT NULL
                )
                """
            )
            scheme = self._meta("identity_scheme")
            identity_key = self._meta("identity_key")
            if scheme is not None and (scheme != _IDENTITY_SCHEME or identity_key is None):
                raise RuntimeError("unsupported or incomplete ledger identity metadata")
            if identity_key is None:
                identity_key = secrets.token_hex(32)
                self._set_meta("identity_key", identity_key)
            key = bytes.fromhex(identity_key)
            if len(key) != 32:
                raise RuntimeError("invalid ledger identity metadata")
            if scheme is None:
                rows = self.connection.execute(
                    "SELECT event_id, session_ref, turn_ref FROM observed_requests"
                ).fetchall()
                # Wrap the legacy digest rather than retaining an unkeyed alias.
                # New requests wrap that same digest, preserving deduplication without raw IDs.
                self.connection.executemany(
                    "UPDATE observed_requests SET event_id = ?, session_ref = ?, turn_ref = ? WHERE event_id = ?",
                    [(_keyed_ref(event, key, "event"), _keyed_ref(session, key, "session"),
                      _keyed_ref(turn, key, "turn"), event) for event, session, turn in rows],
                )
                self._set_meta("identity_scheme", _IDENTITY_SCHEME)
            self._set_meta("schema_version", "2")

    def _meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM ledger_meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO ledger_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @_locked
    def record_observed_request(self, payload: Mapping[str, Any]) -> bool:
        usage = payload.get("usage")
        identity_key = self._meta("identity_key")
        if identity_key is None or self._meta("identity_scheme") != _IDENTITY_SCHEME:
            raise RuntimeError("ledger has not migrated")
        key = bytes.fromhex(identity_key)
        event_id = _opaque_ref(payload.get("api_request_id"), key, "event")
        if not isinstance(usage, Mapping) or event_id is None:
            return False
        session_ref = _opaque_ref(payload.get("session_id"), key, "session")
        turn_ref = _opaque_ref(payload.get("turn_id"), key, "turn")
        with self.connection:
            result = self.connection.execute(
                """
                INSERT OR IGNORE INTO observed_requests (
                    event_id, session_ref, turn_ref, provider, model,
                    input_tokens, output_tokens, total_tokens, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_ref,
                    turn_ref,
                    _label(payload.get("provider")),
                    _label(payload.get("model")),
                    _observed_positive(usage.get("input_tokens")),
                    _observed_positive(usage.get("output_tokens")),
                    None,  # Hermes normalizes/derives total_tokens without source-presence provenance.
                    int(time.time()),
                ),
            )
        return result.rowcount == 1

    @_locked
    def summary(self) -> dict[str, int | None]:
        row = self.connection.execute(
            """
            SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(total_tokens)
            FROM observed_requests
            """
        ).fetchone()
        return {
            "observed_requests": int(row[0]),
            "input_tokens": row[1],
            "output_tokens": row[2],
            "total_tokens": row[3],
        }

    @_locked
    def clear(self) -> int:
        """Delete only observed-request rows; schema metadata remains intact."""
        with self.connection:
            result = self.connection.execute("DELETE FROM observed_requests")
        return max(0, int(result.rowcount))
