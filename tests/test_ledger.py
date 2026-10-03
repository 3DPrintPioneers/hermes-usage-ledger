import sqlite3
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ledger import UsageLedger


class UsageLedgerTests(unittest.TestCase):
    def test_observed_request_is_deduplicated_without_promoting_derived_total(self):
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        payload = {
            "api_request_id": "request-1",
            "session_id": "session-secret",
            "turn_id": "turn-secret",
            "provider": "openai-codex",
            "model": "gpt-5.6-terra",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            },
            "request": {"body": "do not store this"},
            "response": {"assistant_message": {"content": "do not store this"}},
        }

        self.assertTrue(ledger.record_observed_request(payload))
        self.assertFalse(ledger.record_observed_request(payload))

        self.assertEqual(
            ledger.summary(),
            {
                "observed_requests": 1,
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": None,
            },
        )

        stored = ledger.connection.execute(
            "SELECT event_id, session_ref, turn_ref FROM observed_requests"
        ).fetchone()
        self.assertNotIn("request-1", repr(stored))
        self.assertNotIn("session-secret", repr(stored))
        self.assertNotIn("turn-secret", repr(stored))
        self.assertNotIn(
            "request",
            [row[1] for row in ledger.connection.execute("PRAGMA table_info(observed_requests)")],
        )
        self.assertNotIn(
            "response",
            [row[1] for row in ledger.connection.execute("PRAGMA table_info(observed_requests)")],
        )

    def test_zero_or_missing_usage_buckets_remain_none(self):
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()

        self.assertTrue(
            ledger.record_observed_request(
                {
                    "api_request_id": "request-with-no-measured-tokens",
                    "session_id": "session-2",
                    "turn_id": "turn-2",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                }
            )
        )

        self.assertEqual(
            ledger.summary(),
            {
                "observed_requests": 1,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
        )

    def test_ledger_schema_version_is_created_without_touching_observations(self):
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        self.assertEqual(
            ledger.connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone(),
            ("2",),
        )

    def test_unsafe_provider_and_model_labels_are_not_persisted(self):
        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        self.assertTrue(
            ledger.record_observed_request(
                {
                    "api_request_id": "request-safe-labels",
                    "provider": "provider\nCREDENTIAL-DO-NOT-RETAIN",
                    "model": "model with spaces",
                    "usage": {"input_tokens": 1},
                }
            )
        )
        self.assertEqual(
            ledger.connection.execute("SELECT provider, model FROM observed_requests").fetchone(),
            (None, None),
        )

    def test_malformed_hook_payload_is_ignored(self):
        connection = sqlite3.connect(":memory:")
        ledger = UsageLedger(connection)
        ledger.migrate()

        self.assertFalse(ledger.record_observed_request({"usage": None}))
        self.assertEqual(
            ledger.summary(),
            {
                "observed_requests": 0,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
