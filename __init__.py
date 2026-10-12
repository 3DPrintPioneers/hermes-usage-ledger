"""Hermes hook registration for the local usage-ledger plugin."""

from __future__ import annotations

import math
import json
import re
import threading
from functools import wraps
from collections.abc import Mapping
from typing import Any

from .ledger import UsageLedger
from .tally_writer import TallyWriter

PLUGIN_ID = "usage-ledger"
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
)
_LABEL = re.compile(r"[A-Za-z0-9._/:@+-]{1,128}")
_ledger: UsageLedger | None = None
_tally_writer: TallyWriter | None = None
_resource_lock = threading.RLock()
_finalize_tally = True


def _resources_locked(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        with _resource_lock:
            return method(*args, **kwargs)
    return wrapped


@_resources_locked
def _get_ledger() -> UsageLedger:
    global _ledger
    if _ledger is None:
        from plugins.plugin_storage import plugin_db

        candidate = UsageLedger(plugin_db(PLUGIN_ID, "ledger.db"))
        try:
            candidate.migrate()
        except Exception:
            candidate.connection.close()
            raise
        _ledger = candidate
    return _ledger


@_resources_locked
def _get_tally_writer() -> TallyWriter:
    global _tally_writer
    if _tally_writer is None:
        from hermes_constants import get_hermes_home
        from plugins.plugin_storage import plugin_db

        candidate = TallyWriter(
            plugin_db(PLUGIN_ID, "tally-state.db"),
            get_hermes_home() / "tally.md",
        )
        try:
            candidate.migrate()
        except Exception:
            candidate.connection.close()
            raise
        _tally_writer = candidate
    return _tally_writer


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _label(value: object) -> str | None:
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else None


def _numeric(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _safe_usage(value: object) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, int | float] = {}
    for field in _USAGE_FIELDS:
        numeric = _numeric(value.get(field))
        if numeric is not None:
            safe[field] = numeric
    return safe


def record_turn_started(**payload: Any) -> None:
    """Stage only an opaque turn reference and a local start timestamp."""
    turn_id = _identifier(payload.get("turn_id"))
    payload.clear()
    if not _finalize_tally:
        return None
    try:
        _get_tally_writer().record_turn_started(turn_id)
    except Exception:
        # Accounting must never affect the user's agent turn.
        return None


def record_observed_request(**payload: Any) -> None:
    """Store only allowlisted IDs, labels, numeric usage, and duration."""
    api_request_id = _identifier(payload.get("api_request_id"))
    session_id = _identifier(payload.get("session_id"))
    turn_id = _identifier(payload.get("turn_id"))
    provider = _label(payload.get("provider"))
    model = _label(payload.get("response_model")) or _label(payload.get("model"))
    usage = _safe_usage(payload.get("usage"))
    api_duration = _numeric(payload.get("api_duration"))
    payload.clear()
    try:
        _get_ledger().record_observed_request(
            {
                "api_request_id": api_request_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "provider": provider,
                "model": model,
                "usage": usage,
            }
        )
    except Exception:
        # The observed ledger must not suppress independent tally staging.
        pass
    if not _finalize_tally:
        return None
    try:
        _get_tally_writer().stage_request(
            api_request_id=api_request_id,
            turn_id=turn_id,
            usage=usage,
            api_duration=api_duration,
        )
    except Exception:
        # Observer failures must never affect the user's agent turn.
        return None


def record_completed_response(**payload: Any) -> None:
    """Count final text in memory; persist numeric counts and opaque IDs only."""
    turn_id = _identifier(payload.get("turn_id"))
    response = payload.get("assistant_response")
    payload.clear()
    if not _finalize_tally:
        del response
        return None
    try:
        _get_tally_writer().complete_turn(turn_id, response)
    except Exception:
        # A pending numeric journal record is recoverable after a transient file failure.
        return None
    finally:
        # The raw final response is never logged, cached, hashed, or persisted.
        del response


def recover_pending_tally(**payload: Any) -> None:
    """Retry a content-free pending tally projection at finalization boundaries."""
    payload.clear()
    if not _finalize_tally:
        return None
    try:
        _get_tally_writer().recover()
    except Exception:
        return None


@_resources_locked
def _close_resources() -> None:
    global _ledger, _tally_writer
    for resource in (_ledger, _tally_writer):
        if resource is not None:
            try:
                resource.connection.close()
            except Exception:
                pass
    _ledger = None
    _tally_writer = None


def _read_numeric_endpoint(action: str, confirm: bool = False) -> dict[str, Any]:
    from .dashboard import plugin_api

    if action == "clear":
        return plugin_api.clear({"confirm": confirm})
    return {"tally": plugin_api.tally, "diagnostics": plugin_api.diagnostics,
            "summary": plugin_api.summary}[action]()


def numeric_command(raw_args: str) -> str:
    """Public SDK command fallback for a backend whose REST table is already mounted."""
    try:
        body = json.loads(raw_args)
        if not isinstance(body, dict) or body.get("action") not in {"tally", "diagnostics", "summary", "clear"}:
            return json.dumps({"error": "unknown numeric action"})
        action = body["action"]
        confirm = body.get("confirm") is True
        body.clear()
        if action == "clear" and not confirm:
            return json.dumps({"error": "explicit confirmation required"})
        return json.dumps(_read_numeric_endpoint(action, confirm))
    except Exception:
        return json.dumps({"error": "numeric endpoint unavailable"})


def register(ctx: Any) -> None:
    global _finalize_tally
    get_config = getattr(ctx, "get_config", None)
    _finalize_tally = get_config("finalize_tally", True) is not False if callable(get_config) else True
    ctx.register_hook("pre_llm_call", record_turn_started)
    ctx.register_hook("post_api_request", record_observed_request)
    ctx.register_hook("post_llm_call", record_completed_response)
    ctx.register_hook("on_session_finalize", recover_pending_tally)
    ctx.on_unload(_close_resources)
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        register_command("usage-ledger", numeric_command, "Read local numeric usage; clear requires explicit confirmation.")
