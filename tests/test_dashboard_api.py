import importlib.util
import json
import sqlite3
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from ledger import UsageLedger

DASHBOARD_DIR = PROJECT_ROOT / "dashboard"


def load_api_module():
    spec = importlib.util.spec_from_file_location(
        "usage_ledger_dashboard_api",
        DASHBOARD_DIR / "plugin_api.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DashboardApiTests(unittest.TestCase):
    def test_manifest_hides_dashboard_tab_and_declares_summary_api(self):
        manifest = json.loads((DASHBOARD_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "usage-ledger")
        self.assertTrue(manifest["tab"]["hidden"])
        self.assertEqual(manifest["api"], "plugin_api.py")
        self.assertTrue((DASHBOARD_DIR / manifest["entry"]).is_file())

        api = load_api_module()
        paths = {route.path for route in api.router.routes}
        self.assertEqual(paths, {"/health", "/summary", "/tally", "/diagnostics", "/clear"})
        self.assertEqual(api.health(), {"status": "ok", "storage": "local-only"})

    def test_diagnostics_reports_read_only_source_and_durable_state(self):
        api = load_api_module()
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        state = sqlite3.connect(":memory:")
        state.executescript(
            """
            CREATE TABLE tally_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO tally_meta VALUES ('schema_version', '2');
            INSERT INTO tally_meta VALUES ('baseline_sequence', '10');
            INSERT INTO tally_meta VALUES ('provider_reseed_pending', '0');
            CREATE TABLE completed_turns (sequence INTEGER PRIMARY KEY, status TEXT NOT NULL);
            INSERT INTO completed_turns VALUES (1, 'applied');
            """
        )
        api._open_readonly_ledger = lambda: ledger.connection
        api._open_tally_state = lambda: state
        api.read_tally_snapshot = lambda: {
            "updated": "2026-09-09T04:30:34-04:00",
            "current": {"words": 10},
            "running": {"words": 110},
        }

        self.assertEqual(
            api.diagnostics(),
            {
                "status": "ok",
                "storage": "local-only",
                "source_scope": "active profile tally.md",
                "refresh_interval_seconds": 30,
                "tally_updated": "2026-09-09T04:30:34-04:00",
                "tally_state": {
                    "schema_version": 2,
                    "baseline_sequence": 10,
                    "completed_turns": 1,
                    "pending_turns": 0,
                    "baseline_adopted": True,
                },
                "observed_ledger": {
                    "schema_version": 2,
                    "observed_requests": 0,
                },
            },
        )

    def test_clear_requires_explicit_confirmation_and_keeps_tally_unchanged(self):
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        ledger.record_observed_request(
            {
                "api_request_id": "api-clear-1",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
        api = load_api_module()
        api._open_ledger = lambda: ledger
        tally = {"updated": "same", "current": {"words": 1}, "running": {"words": 2}}
        api.read_tally_snapshot = lambda: tally

        with self.assertRaises(HTTPException) as error:
            api.clear()
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(ledger.summary()["observed_requests"], 1)

        self.assertEqual(
            api.clear({"confirm": True}),
            {"cleared": True, "scope": "observed-request-ledger", "deleted_records": 1},
        )
        self.assertEqual(ledger.summary(), {
            "observed_requests": 0,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        })
        self.assertEqual(api.tally(), tally)

    def test_tally_exposes_only_labeled_usage_accounting_fields(self):
        api = load_api_module()
        expected = {
            "updated": "2026-09-09T04:30:34-04:00",
            "current": {"words": 10},
            "running": {"words": 110},
        }
        api.read_tally_snapshot = lambda: expected

        self.assertEqual(api.tally(), expected)

    def test_summary_reads_the_ledger_without_exposing_content(self):
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        ledger.record_observed_request(
            {
                "api_request_id": "api-1",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
                "response": {"content": "private"},
            }
        )

        api = load_api_module()
        api._open_ledger = lambda: ledger

        self.assertEqual(
            api.summary(),
            {
                "observed_requests": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
