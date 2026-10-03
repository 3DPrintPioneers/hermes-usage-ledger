"""Scoped read-only API for the local usage ledger."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_ID = "usage-ledger"
_REFRESH_INTERVAL_SECONDS = 30


def _usage_ledger_type():
    spec = importlib.util.spec_from_file_location(
        "usage_ledger_dashboard_shared",
        _PLUGIN_ROOT / "ledger.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("usage-ledger shared module could not load")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UsageLedger


def read_tally_snapshot() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location(
        "usage_ledger_tally_snapshot",
        _PLUGIN_ROOT / "tally_snapshot.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("usage-ledger tally reader could not load")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.read_tally_snapshot()


def _open_ledger() -> Any:
    from plugins.plugin_storage import plugin_db

    ledger = _usage_ledger_type()(plugin_db(_PLUGIN_ID, "ledger.db"))
    ledger.migrate()
    return ledger


def _open_readonly_db(filename: str) -> sqlite3.Connection | None:
    """Open existing plugin data without creating or migrating it."""
    from hermes_constants import get_hermes_home

    path = get_hermes_home() / "plugin-data" / _PLUGIN_ID / filename
    if not path.is_file():
        return None
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def _open_readonly_ledger() -> sqlite3.Connection | None:
    return _open_readonly_db("ledger.db")


def _open_tally_state() -> sqlite3.Connection | None:
    return _open_readonly_db("tally-state.db")


def _meta_value(
    connection: sqlite3.Connection | None,
    key: str,
    table: str = "tally_meta",
) -> str | None:
    if connection is None:
        return None
    if table not in {"tally_meta", "ledger_meta"}:
        return None
    try:
        row = connection.execute(f"SELECT value FROM {table} WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _count(connection: sqlite3.Connection | None, query: str) -> int | None:
    if connection is None:
        return None
    try:
        row = connection.execute(query).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else int(row[0])


@router.get("/health")
def health():
    return {"status": "ok", "storage": "local-only"}


@router.get("/summary")
def summary():
    return _open_ledger().summary()


@router.get("/diagnostics")
def diagnostics():
    """Report safe lifecycle/source state without mutating plugin data."""
    ledger = _open_readonly_ledger()
    state = _open_tally_state()
    try:
        try:
            tally = read_tally_snapshot()
        except Exception:
            tally = {}
        schema_version = _integer(_meta_value(state, "schema_version"))
        baseline_sequence = _integer(_meta_value(state, "baseline_sequence"))
        ledger_schema = _integer(_meta_value(ledger, "schema_version", "ledger_meta"))
        return {
            "status": "ok",
            "storage": "local-only",
            "source_scope": "active profile tally.md",
            "refresh_interval_seconds": _REFRESH_INTERVAL_SECONDS,
            "tally_updated": tally.get("updated"),
            "tally_state": {
                "schema_version": schema_version,
                "baseline_sequence": baseline_sequence,
                "completed_turns": _count(state, "SELECT COUNT(*) FROM completed_turns"),
                "pending_turns": _count(
                    state,
                    "SELECT COUNT(*) FROM completed_turns WHERE status = 'pending'",
                ),
                "baseline_adopted": None if baseline_sequence is None else baseline_sequence > 0,
            },
            "observed_ledger": {
                "schema_version": ledger_schema,
                "observed_requests": _count(ledger, "SELECT COUNT(*) FROM observed_requests"),
            },
        }
    finally:
        for connection in (ledger, state):
            if connection is not None:
                connection.close()


@router.get("/tally")
def tally():
    """Expose only structured accounting fields from the active profile tally."""
    return read_tally_snapshot()


@router.post("/clear")
def clear(body: dict[str, Any] | None = None):
    """Clear only observed-request rows after an explicit confirmation."""
    if not isinstance(body, dict) or body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="explicit confirmation required")
    deleted_records = _open_ledger().clear()
    return {
        "cleared": True,
        "scope": "observed-request-ledger",
        "deleted_records": deleted_records,
    }
