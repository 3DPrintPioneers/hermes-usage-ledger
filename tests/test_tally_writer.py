import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class ResponseMeasurementTests(unittest.TestCase):
    def test_measure_response_text_returns_numeric_counts_without_text(self):
        from tally_writer import measure_response_text

        fence = "`" * 3
        response = f"Alpha  beta\n\n{fence}python\nx = 1\n{fence}\nInline `ok`."

        measured = measure_response_text(response)

        self.assertEqual(
            measured,
            {"words": 9, "characters": 45, "coding_characters": 8},
        )
        self.assertNotIn("text", measured)

    def test_measure_response_text_excludes_terminal_hermes_usage_footer(self):
        from tally_writer import measure_response_text

        response = "Ready.\n\n---\n\n**Hermes usage:** 1 word · 6 characters"

        self.assertEqual(
            measure_response_text(response),
            {"words": 1, "characters": 6, "coding_characters": 0},
        )


class TallyWriterTests(unittest.TestCase):
    def test_legacy_root_baseline_preserves_missing_work_count(self):
        from contextlib import closing
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            tally.write_text('# Hermes global usage tally\nUpdated: legacy\nWords Running tally: 40.\n'
                             'Working Time Running tally: 30.5 seconds measured / 2 unavailable.\n'
                             '\n## History\nWorking Time Unavailable Responses: 999\n', encoding='utf-8')
            writer = TallyWriter(db, tally)
            writer.migrate()
            writer.record_turn_started('fixture-legacy', started_at=10)
            writer.complete_turn('fixture-legacy', 'one', ended_at=13)
            snapshot = read_tally_snapshot(tally)
            self.assertEqual(snapshot['running']['working_seconds'], 33.5)
            self.assertEqual(snapshot['working_time_missing_responses'], 2)
            self.assertIn('incomplete', tally.read_text(encoding='utf-8'))

    def test_missing_working_durations_preserve_known_baseline_and_future_additions(self):
        import json
        from contextlib import closing
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        with tempfile.TemporaryDirectory() as temp_dir, closing(
            sqlite3.connect(Path(temp_dir) / "state.db")
        ) as connection:
            root = Path(temp_dir)
            tally_path = root / "tally.md"
            tally_path.write_text(
                "# Tally\n\n## Current response\n"
                "Working Time Running tally: 100 seconds\n"
                "Working Time Unavailable Responses: 7\n",
                encoding="utf-8",
            )
            writer = TallyWriter(connection, tally_path)
            writer.migrate()
            writer.complete_turn("missing-start", "Example", ended_at=5)
            writer.record_turn_started("measured", started_at=10)
            writer.complete_turn("measured", "Example", ended_at=30)
            baseline = connection.execute(
                "SELECT value FROM tally_meta WHERE key = 'baseline'"
            ).fetchone()[0]
            # Simulate an already-applied legacy projection at upgrade time.
            with connection:
                connection.execute(
                    "UPDATE tally_meta SET value = '[]' WHERE key = 'observed_sum_fields'"
                )
            writer.migrate()
            snapshot = read_tally_snapshot(tally_path)
            self.assertEqual(snapshot["current"]["working_seconds"], 20)
            self.assertEqual(snapshot["running"]["working_seconds"], 120)
            self.assertEqual(snapshot["working_time_missing_responses"], 8)
            self.assertIn("incomplete", tally_path.read_text(encoding="utf-8"))
            self.assertEqual(connection.execute(
                "SELECT value FROM tally_meta WHERE key = 'baseline'"
            ).fetchone()[0], baseline)
            self.assertIn("working_seconds", json.loads(connection.execute(
                "SELECT value FROM tally_meta WHERE key = 'observed_sum_fields'"
            ).fetchone()[0]))
            writer.record_turn_started("next", started_at=40)
            writer.complete_turn("next", "Example", ended_at=50)
            self.assertEqual(read_tally_snapshot(tally_path)["running"]["working_seconds"], 130)
            reopened = TallyWriter(connection, tally_path)
            reopened.migrate()
            self.assertFalse(reopened.complete_turn("next", "Example", ended_at=60))
            self.assertEqual(read_tally_snapshot(tally_path)["running"]["working_seconds"], 130)
            self.assertEqual(read_tally_snapshot(tally_path)["working_time_missing_responses"], 8)

    def test_completed_response_without_request_observations_writes_text_and_working_metrics(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tally_path = root / "tally.md"
            tally_path.write_text(
                """# Hermes usage tally

## Current response
Updated: legacy
Thinking time: unavailable
Working time: 5 seconds (wall-clock proxy)
Word count: 1
Character count: 2
Coding character count: 3
Thinking Time Running tally: unavailable
Working Time Running tally: 5 seconds
Words Running tally: 10
Character Running tally: 20
Coding Character Running tally: 3
Thinking Time Unavailable Responses: 4
Working Time Unavailable Responses: 2
""",
                encoding="utf-8",
            )
            connection = sqlite3.connect(root / "tally-state.db")
            self.addCleanup(connection.close)
            writer = TallyWriter(connection, tally_path)
            writer.migrate()
            writer.record_turn_started("turn-1", started_at=100.0)

            self.assertTrue(writer.complete_turn("turn-1", "one two", ended_at=105.0))

            snapshot = read_tally_snapshot(tally_path)
            self.assertEqual(snapshot["current"]["words"], 2)
            self.assertEqual(snapshot["current"]["characters"], 7)
            self.assertEqual(snapshot["current"]["coding_characters"], 0)
            self.assertEqual(snapshot["current"]["working_seconds"], 5)
            self.assertIsNone(snapshot["current"]["thinking_seconds"])
            self.assertIsNone(snapshot["current"]["calls"])
            self.assertEqual(snapshot["running"]["words"], 12)
            self.assertEqual(snapshot["running"]["characters"], 27)
            self.assertEqual(snapshot["running"]["coding_characters"], 3)
            self.assertEqual(snapshot["running"]["working_seconds"], 10)
            written = tally_path.read_text(encoding="utf-8")
            self.assertNotIn("one two", written)
            self.assertIn("Tally finalizer commit:", written)
            connection.close()

    def test_staged_requests_materialize_exact_final_turn_metrics(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        tally_path = root / "tally.md"
        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        writer = TallyWriter(connection, tally_path)
        writer.migrate()
        writer.record_turn_started("turn-2", started_at=100.0)
        self.assertTrue(
            writer.stage_request(
                api_request_id="api-1",
                turn_id="turn-2",
                usage={
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "cache_read_tokens": 2,
                    "cache_write_tokens": 1,
                    "reasoning_tokens": 1,
                    "total_tokens": 18,
                },
                api_duration=1.5,
            )
        )
        self.assertTrue(
            writer.stage_request(
                api_request_id="api-2",
                turn_id="turn-2",
                usage={
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "cache_read_tokens": 1,
                    "cache_write_tokens": 2,
                    "reasoning_tokens": 1,
                    "total_tokens": 13,
                },
                api_duration=2.0,
            )
        )
        self.assertFalse(tally_path.exists())

        self.assertTrue(writer.complete_turn("turn-2", "two words", ended_at=105.0))

        snapshot = read_tally_snapshot(tally_path)
        self.assertEqual(snapshot["current"]["input_tokens"], 17)
        self.assertEqual(snapshot["current"]["output_tokens"], 7)
        self.assertEqual(snapshot["current"]["cache_read_tokens"], 3)
        self.assertEqual(snapshot["current"]["cache_write_tokens"], 3)
        self.assertEqual(snapshot["current"]["reasoning_tokens"], 2)
        self.assertEqual(snapshot["current"]["total_tokens"], 30)
        self.assertEqual(snapshot["current"]["expanded_activity_tokens"], 32)
        self.assertEqual(snapshot["current"]["calls"], 2)
        self.assertEqual(snapshot["current"]["thinking_seconds"], 3.5)
        self.assertEqual(snapshot["current"]["working_seconds"], 5)
        self.assertEqual(snapshot["current"]["characters"], 9)

    def test_failed_atomic_replace_leaves_pending_turn_for_restart_recovery(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        tally_path = root / "tally.md"
        old_tally = "# Hermes usage tally\n\n## Current response\nWords Running tally: 8\n"
        tally_path.write_text(old_tally, encoding="utf-8")

        def fail_replace(_source, _destination):
            raise OSError("injected replace failure")

        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        writer = TallyWriter(connection, tally_path, replace=fail_replace)
        writer.migrate()
        self.assertFalse(writer.complete_turn("turn-recover", "three words now", ended_at=9.0))
        self.assertEqual(tally_path.read_text(encoding="utf-8"), old_tally)
        self.assertEqual(
            connection.execute("SELECT status FROM completed_turns").fetchone()[0], "pending"
        )
        connection.close()

        reopened = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(reopened.close)
        recovered = TallyWriter(reopened, tally_path)
        recovered.migrate()
        snapshot = read_tally_snapshot(tally_path)
        self.assertEqual(snapshot["current"]["words"], 3)
        self.assertEqual(snapshot["running"]["words"], 11)
        self.assertEqual(
            reopened.execute("SELECT status FROM completed_turns").fetchone()[0], "applied"
        )

    def test_restart_repairs_a_crash_after_replace_before_marking_applied(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        class CrashAfterReplaceWriter(TallyWriter):
            def _mark_pending_applied(self):
                raise RuntimeError("injected after-replace crash")

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        tally_path = root / "tally.md"
        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        writer = CrashAfterReplaceWriter(connection, tally_path)
        writer.migrate()
        with self.assertRaisesRegex(RuntimeError, "after-replace"):
            writer.complete_turn("turn-after-replace", "one", ended_at=4.0)
        self.assertTrue(tally_path.is_file())
        self.assertEqual(
            connection.execute("SELECT status FROM completed_turns").fetchone()[0], "pending"
        )
        connection.close()

        reopened = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(reopened.close)
        recovered = TallyWriter(reopened, tally_path)
        recovered.migrate()
        self.assertEqual(read_tally_snapshot(tally_path)["running"]["words"], 1)
        self.assertEqual(
            reopened.execute("SELECT status FROM completed_turns").fetchone()[0], "applied"
        )

    def test_two_connections_do_not_leave_a_torn_tally_projection(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        state_path = root / "tally-state.db"
        tally_path = root / "tally.md"
        first_connection = sqlite3.connect(state_path, check_same_thread=False)
        second_connection = sqlite3.connect(state_path, check_same_thread=False)
        self.addCleanup(first_connection.close)
        self.addCleanup(second_connection.close)
        first = TallyWriter(first_connection, tally_path)
        second = TallyWriter(second_connection, tally_path)
        first.migrate()
        second.migrate()
        first.record_turn_started("turn-first", started_at=1.0)
        second.record_turn_started("turn-second", started_at=1.0)

        reached_replace = threading.Event()
        release_replace = threading.Event()
        second_entered = threading.Event()
        second_finished = threading.Event()
        results = []
        original_replace = first._atomic_replace

        def delayed_replace(contents):
            reached_replace.set()
            self.assertTrue(release_replace.wait(timeout=5))
            original_replace(contents)

        first._atomic_replace = delayed_replace

        def complete_first():
            results.append(first.complete_turn("turn-first", "one", ended_at=2.0))

        def complete_second():
            second_entered.set()
            results.append(second.complete_turn("turn-second", "two words", ended_at=3.0))
            second_finished.set()

        first_thread = threading.Thread(target=complete_first)
        second_thread = threading.Thread(target=complete_second)
        first_thread.start()
        self.assertTrue(reached_replace.wait(timeout=5))
        second_thread.start()
        self.assertTrue(second_entered.wait(timeout=5))
        self.assertFalse(second_finished.wait(timeout=0.2))
        release_replace.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(results, [True, True])

        snapshot = read_tally_snapshot(tally_path)
        self.assertEqual(snapshot["current"]["words"], 2)
        self.assertEqual(snapshot["running"]["words"], 3)
        self.assertEqual(snapshot["running"]["characters"], 12)

    def test_missing_or_zero_usage_bucket_is_never_represented_as_zero(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        writer = TallyWriter(connection, root / "tally.md")
        writer.migrate()
        writer.stage_request(
            api_request_id="api-known",
            turn_id="turn-known",
            usage={"output_tokens": 3},
            api_duration=None,
        )
        writer.complete_turn("turn-known", "one", ended_at=1.0)
        writer.stage_request(
            api_request_id="api-zero",
            turn_id="turn-zero",
            usage={"output_tokens": 0},
            api_duration=None,
        )
        writer.complete_turn("turn-zero", "two", ended_at=2.0)

        snapshot = read_tally_snapshot(root / "tally.md")
        self.assertIsNone(snapshot["current"]["output_tokens"])
        self.assertIsNone(snapshot["running"]["output_tokens"])
        self.assertNotEqual(snapshot["current"]["output_tokens"], 0)
        self.assertNotEqual(snapshot["running"]["output_tokens"], 0)

    def test_duplicate_request_and_final_turn_are_noops(self):
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        tally_path = root / "tally.md"
        writer = TallyWriter(connection, tally_path)
        writer.migrate()
        self.assertTrue(
            writer.stage_request(
                api_request_id="api-duplicate", turn_id="turn-duplicate", usage={"input_tokens": 5}, api_duration=1
            )
        )
        self.assertFalse(
            writer.stage_request(
                api_request_id="api-duplicate", turn_id="turn-duplicate", usage={"input_tokens": 5}, api_duration=1
            )
        )
        self.assertTrue(writer.complete_turn("turn-duplicate", "one", ended_at=2))
        initial_tally = tally_path.read_bytes()
        self.assertFalse(writer.complete_turn("turn-duplicate", "one", ended_at=2))
        self.assertEqual(tally_path.read_bytes(), initial_tally)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM staged_requests").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM completed_turns").fetchone()[0], 1)

    def test_tally_state_schema_version_is_created(self):
        from tally_writer import TallyWriter

        connection = sqlite3.connect(":memory:")
        writer = TallyWriter(connection, Path("unused-tally.md"))
        writer.migrate()
        self.assertEqual(
            connection.execute(
                "SELECT value FROM tally_meta WHERE key = 'schema_version'"
            ).fetchone(),
            ("2",),
        )

    def test_late_request_observation_rematerializes_an_already_completed_turn(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        tally_path = root / "tally.md"
        writer = TallyWriter(connection, tally_path)
        writer.migrate()
        writer.complete_turn("turn-late", "one", ended_at=1.0)
        self.assertIsNone(read_tally_snapshot(tally_path)["current"]["calls"])

        self.assertTrue(
            writer.stage_request(
                api_request_id="api-late",
                turn_id="turn-late",
                usage={"input_tokens": 5},
                api_duration=1.0,
            )
        )
        snapshot = read_tally_snapshot(tally_path)
        self.assertEqual(snapshot["current"]["input_tokens"], 5)
        self.assertEqual(snapshot["current"]["calls"], 1)
        self.assertEqual(snapshot["running"]["input_tokens"], 5)
        self.assertEqual(snapshot["running"]["calls"], 1)

    def test_adopted_running_baseline_uses_latest_tally_values_and_tracks_future_observations(self):
        from tally_snapshot import read_tally_snapshot
        from tally_writer import TallyWriter

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        tally_path = root / "tally.md"
        tally_path.write_text(
            """# Hermes usage tally

## Current response
Input tokens: 6
Output tokens: 3
Cache-read tokens: 4
Cache-write tokens: unavailable
Reasoning tokens: 2
Total tokens: unavailable
Calls: 2
Thinking time: 1.5 seconds
Working time: 10 seconds
Word count: 10
Character count: 20
Coding character count: 1
Input Tokens Running tally: 100
Output Tokens Running tally: 200
Cache-read Tokens Running tally: 300
Cache-write Tokens Running tally: 0
Reasoning Tokens Running tally: 400
Total Tokens Running tally: 1000
Calls Running tally: 600
Thinking Time Running tally: 700 seconds
Working Time Running tally: 10 seconds
Words Running tally: 10
Character Running tally: 20
Coding Character Running tally: 1
Thinking Time Unavailable Responses: 9
Working Time Unavailable Responses: 8
""",
            encoding="utf-8",
        )
        connection = sqlite3.connect(root / "tally-state.db")
        self.addCleanup(connection.close)
        writer = TallyWriter(connection, tally_path)
        writer.migrate()
        writer.record_turn_started("before-reset", started_at=10)
        writer.stage_request(
            api_request_id="before-reset-request-1",
            turn_id="before-reset",
            usage={"input_tokens": 4, "output_tokens": 2, "reasoning_tokens": 1},
            api_duration=0.5,
        )
        writer.stage_request(
            api_request_id="before-reset-request-2",
            turn_id="before-reset",
            usage={"input_tokens": 2, "output_tokens": 1, "reasoning_tokens": 1},
            api_duration=1.0,
        )
        writer.complete_turn("before-reset", "one", ended_at=15)
        tally_path.write_text(
            """# Hermes usage tally

## Current response
Input tokens: 6
Output tokens: 3
Cache-read tokens: 4
Cache-write tokens: unavailable
Reasoning tokens: 2
Total tokens: unavailable
Calls: 2
Thinking time: 1.5 seconds
Working time: 10 seconds
Word count: 10
Character count: 20
Coding character count: 1
Input Tokens Running tally: 100
Output Tokens Running tally: 200
Cache-read Tokens Running tally: 300
Cache-write Tokens Running tally: 0
Reasoning Tokens Running tally: 400
Total Tokens Running tally: 1000
Calls Running tally: 600
Thinking Time Running tally: 700 seconds
Working Time Running tally: 10 seconds
Words Running tally: 10
Character Running tally: 20
Coding Character Running tally: 1
Thinking Time Unavailable Responses: 9
Working Time Unavailable Responses: 8
""",
            encoding="utf-8",
        )

        writer.adopt_running_baseline_from_tally()

        snapshot = read_tally_snapshot(tally_path)
        self.assertEqual(snapshot["running"]["input_tokens"], 100)
        self.assertEqual(snapshot["running"]["output_tokens"], 200)
        self.assertEqual(snapshot["running"]["cache_read_tokens"], 300)
        self.assertEqual(snapshot["running"]["cache_write_tokens"], 0)
        self.assertEqual(snapshot["running"]["reasoning_tokens"], 400)
        self.assertEqual(snapshot["running"]["total_tokens"], 600)
        self.assertEqual(snapshot["running"]["expanded_activity_tokens"], 1000)
        self.assertEqual(snapshot["running"]["calls"], 600)
        self.assertEqual(snapshot["running"]["thinking_seconds"], 700)
        self.assertEqual(snapshot["running"]["working_seconds"], 10)
        self.assertEqual(snapshot["running"]["words"], 10)
        written = tally_path.read_text(encoding="utf-8")
        self.assertIn("## Provider running-total baseline", written)
        self.assertIn("Thinking Time Unavailable Responses: 9", written)
        self.assertIn("Working Time Unavailable Responses: 8", written)

        writer.record_turn_started("after-reset", started_at=20)
        writer.stage_request(
            api_request_id="after-reset-request",
            turn_id="after-reset",
            usage={"input_tokens": 5},
            api_duration=2,
        )
        writer.complete_turn("after-reset", "two", ended_at=25)

        snapshot = read_tally_snapshot(tally_path)
        self.assertEqual(snapshot["running"]["input_tokens"], 105)
        self.assertEqual(snapshot["running"]["output_tokens"], 200)
        self.assertEqual(snapshot["running"]["cache_read_tokens"], 300)
        self.assertEqual(snapshot["running"]["cache_write_tokens"], 0)
        self.assertEqual(snapshot["running"]["reasoning_tokens"], 400)
        self.assertEqual(snapshot["running"]["total_tokens"], 605)
        self.assertEqual(snapshot["running"]["expanded_activity_tokens"], 1005)
        self.assertEqual(snapshot["running"]["calls"], 601)
        self.assertEqual(snapshot["running"]["thinking_seconds"], 702)
        self.assertEqual(snapshot["running"]["working_seconds"], 15)
        self.assertEqual(snapshot["running"]["words"], 11)
