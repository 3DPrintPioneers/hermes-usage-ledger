import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class RealPluginLifecycleTests(unittest.TestCase):
    def test_narrow_hooks_finalize_one_content_free_tally_turn(self):
        from tally_snapshot import read_tally_snapshot

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            package = home / "plugins" / "usage-ledger"
            package.parent.mkdir(parents=True)
            shutil.copytree(
                PROJECT_ROOT,
                package,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "tests"),
            )
            (home / "config.yaml").write_text(
                "plugins:\n  enabled: [usage-ledger]\n", encoding="utf-8"
            )
            bundled = home / "empty-bundled"
            bundled.mkdir()
            assistant_text = "final reply with `x`"
            prompt_sentinel = "PROMPT-DO-NOT-RETAIN-72a1"
            response_sentinel = "RESPONSE-DO-NOT-RETAIN-b91e"
            tool_sentinel = "TOOL-DO-NOT-RETAIN-c20d"
            credential_sentinel = "CREDENTIAL-DO-NOT-RETAIN-e32f"
            request_id = "REQUEST-DO-NOT-RETAIN-a63c"

            with patch.dict(
                os.environ,
                {"HERMES_HOME": str(home), "HERMES_BUNDLED_PLUGINS": str(bundled)},
                clear=False,
            ):
                from hermes_cli.plugins import get_plugin_manager

                manager = get_plugin_manager()
                try:
                    manager.discover_and_load()
                    manager.invoke_hook(
                        "pre_llm_call", turn_id="turn-test-1", user_message=prompt_sentinel
                    )
                    payload = {
                        "api_request_id": request_id,
                        "session_id": "session-test-1",
                        "turn_id": "turn-test-1",
                        "provider": "test-provider",
                        "model": "test-model",
                        "response_model": "served-model",
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 3,
                            "cache_read_tokens": 2,
                            "cache_write_tokens": 1,
                            "reasoning_tokens": 1,
                            "total_tokens": 19,
                            "unknown_payload": credential_sentinel,
                        },
                        "api_duration": 1.25,
                        "response": {"content": response_sentinel},
                        "assistant_message": {"content": response_sentinel},
                        "user_message": prompt_sentinel,
                        "tool_arguments": {"secret": tool_sentinel},
                    }
                    manager.invoke_hook("post_api_request", **payload)
                    manager.invoke_hook("post_api_request", **payload)
                    manager.invoke_hook(
                        "post_llm_call",
                        turn_id="turn-test-1",
                        assistant_response=assistant_text,
                        user_message=prompt_sentinel,
                        conversation_history=[{"content": response_sentinel}],
                    )
                    manager.invoke_hook(
                        "post_llm_call", turn_id="turn-test-1", assistant_response=assistant_text
                    )
                    manager.invoke_hook("on_session_finalize")
                finally:
                    manager.unload()

            data_dir = home / "plugin-data" / "usage-ledger"
            ledger_path = data_dir / "ledger.db"
            tally_state_path = data_dir / "tally-state.db"
            tally_path = home / "tally.md"
            self.assertTrue(ledger_path.is_file())
            self.assertTrue(tally_state_path.is_file())
            self.assertTrue(tally_path.is_file())

            connection = sqlite3.connect(ledger_path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens) "
                        "FROM observed_requests"
                    ).fetchone(),
                    (1, 12, 3),
                )
                columns = {row[1] for row in connection.execute("PRAGMA table_info(observed_requests)")}
                self.assertFalse({"request", "response", "content", "assistant_message"} & columns)
            finally:
                connection.close()

            snapshot = read_tally_snapshot(tally_path)
            self.assertEqual(snapshot["current"]["input_tokens"], 12)
            self.assertEqual(snapshot["current"]["output_tokens"], 3)
            self.assertEqual(snapshot["current"]["cache_read_tokens"], 2)
            self.assertEqual(snapshot["current"]["cache_write_tokens"], 1)
            self.assertEqual(snapshot["current"]["reasoning_tokens"], 1)
            self.assertEqual(snapshot["current"]["total_tokens"], 18)
            self.assertEqual(snapshot["current"]["expanded_activity_tokens"], 19)
            self.assertEqual(snapshot["current"]["calls"], 1)
            self.assertEqual(snapshot["current"]["thinking_seconds"], 1.25)
            self.assertEqual(snapshot["current"]["words"], 4)
            self.assertEqual(snapshot["current"]["characters"], 20)
            self.assertEqual(snapshot["current"]["coding_characters"], 1)
            self.assertEqual(snapshot["running"]["input_tokens"], 12)
            self.assertEqual(snapshot["running"]["output_tokens"], 3)
            self.assertEqual(snapshot["running"]["cache_read_tokens"], 2)
            self.assertEqual(snapshot["running"]["cache_write_tokens"], 1)
            self.assertEqual(snapshot["running"]["reasoning_tokens"], 1)
            self.assertEqual(snapshot["running"]["total_tokens"], 18)
            self.assertEqual(snapshot["running"]["expanded_activity_tokens"], 19)
            self.assertEqual(snapshot["running"]["calls"], 1)
            self.assertEqual(snapshot["running"]["thinking_seconds"], 1.25)
            self.assertEqual(snapshot["running"]["words"], 4)
            self.assertEqual(snapshot["running"]["characters"], 20)
            self.assertEqual(snapshot["running"]["coding_characters"], 1)

            forbidden = [
                prompt_sentinel.encode(),
                response_sentinel.encode(),
                tool_sentinel.encode(),
                credential_sentinel.encode(),
                request_id.encode(),
                assistant_text.encode(),
            ]
            for artifact in [*data_dir.glob("*"), tally_path]:
                if artifact.is_file():
                    data = artifact.read_bytes()
                    for sentinel in forbidden:
                        self.assertNotIn(sentinel, data, artifact)

    def test_reopening_finalizer_state_reproduces_the_same_tally_without_duplicate_rows(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        with tempfile.TemporaryDirectory() as temp_dir:
            tally_path = Path(temp_dir) / "tally.md"
            state_path = Path(temp_dir) / "tally-state.db"
            first = sqlite3.connect(state_path)
            writer = TallyWriter(first, tally_path)
            writer.migrate()
            writer.record_turn_started("turn-reopen-1", started_at=100.0)
            writer.stage_request(
                api_request_id="request-reopen-1",
                turn_id="turn-reopen-1",
                usage={
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "cache_read_tokens": 2,
                    "cache_write_tokens": 1,
                    "reasoning_tokens": 1,
                },
                api_duration=1.25,
            )
            self.assertTrue(writer.complete_turn("turn-reopen-1", "final reply", ended_at=103.0))
            before_snapshot = read_tally_snapshot(tally_path)
            before_bytes = tally_path.read_bytes()
            first.close()

            second = sqlite3.connect(state_path)
            reopened = TallyWriter(second, tally_path)
            reopened.migrate()
            reopened.recover()
            self.assertEqual(read_tally_snapshot(tally_path), before_snapshot)
            self.assertEqual(tally_path.read_bytes(), before_bytes)
            self.assertEqual(
                second.execute("SELECT COUNT(*), SUM(input_tokens) FROM staged_requests").fetchone(),
                (1, 12),
            )
            self.assertEqual(
                second.execute("SELECT COUNT(*), SUM(words) FROM completed_turns WHERE status = 'applied'").fetchone(),
                (1, 2),
            )
            self.assertEqual(
                second.execute("SELECT COUNT(*) FROM completed_turns WHERE status = 'pending'").fetchone()[0],
                0,
            )
            second.close()

if __name__ == "__main__":
    unittest.main()
