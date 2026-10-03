import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


class WorkingTimePolicyTests(unittest.TestCase):
    def test_unknown_legacy_baseline_remains_unavailable(self):
        from tally_writer import TallyWriter
        from tally_snapshot import read_tally_snapshot

        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            tally.write_text('## Current response\nWorking Time Running tally: unavailable\n', encoding='utf-8')
            writer = TallyWriter(db, tally)
            writer.migrate()
            writer.record_turn_started('measured', started_at=10)
            writer.complete_turn('measured', 'Example', ended_at=30)
            self.assertEqual(read_tally_snapshot(tally)['current']['working_seconds'], 20)
            self.assertIsNone(read_tally_snapshot(tally)['running']['working_seconds'])

    def test_migration_serializes_policy_update_with_baseline_adoption(self):
        from tally_writer import TallyWriter
        import json

        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(Path(directory) / 'state.db')) as first, closing(sqlite3.connect(Path(directory) / 'state.db', timeout=0)) as second:
            tally = Path(directory) / 'tally.md'
            tally.write_text('## Current response\nWorking Time Running tally: 100 seconds\nInput Tokens Running tally: 10\n', encoding='utf-8')
            initial = TallyWriter(first, tally)
            initial.migrate()
            initial.record_turn_started('one', started_at=10)
            initial.complete_turn('one', 'Example', ended_at=30)
            with first:
                first.execute("UPDATE tally_meta SET value = '[]' WHERE key = 'observed_sum_fields'")
            competitor = TallyWriter(second, tally)
            blocked = []

            class RacingMigration(TallyWriter):
                fired = False
                def _observed_sum_fields(self):
                    fields = super()._observed_sum_fields()
                    if not self.fired:
                        self.fired = True
                        try:
                            competitor.adopt_running_baseline_from_tally()
                        except sqlite3.OperationalError as error:
                            if 'locked' not in str(error):
                                raise
                            blocked.append(True)
                    return fields

            RacingMigration(first, tally).migrate()
            self.assertEqual(blocked, [True])
            self.assertTrue(competitor.adopt_running_baseline_from_tally())
            fields = json.loads(first.execute("SELECT value FROM tally_meta WHERE key = 'observed_sum_fields'").fetchone()[0])
            self.assertIn('input_tokens', fields)
            self.assertIn('working_seconds', fields)

    def test_baseline_adoption_does_not_double_count_concurrent_completion(self):
        from tally_writer import TallyWriter
        from tally_snapshot import read_tally_snapshot

        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(Path(directory) / 'state.db')) as first, closing(sqlite3.connect(Path(directory) / 'state.db', timeout=0)) as second:
            tally = Path(directory) / 'tally.md'
            tally.write_text('## Current response\nWorking Time Running tally: 100 seconds\n', encoding='utf-8')
            initial = TallyWriter(first, tally)
            initial.migrate()
            initial.record_turn_started('one', started_at=10)
            initial.complete_turn('one', 'Example', ended_at=30)
            competitor = TallyWriter(second, tally)
            competitor.record_turn_started('two', started_at=40)
            blocked = []

            class RacingAdoption(TallyWriter):
                fired = False
                def _completed_rows(self):
                    rows = super()._completed_rows()
                    if not self.fired:
                        self.fired = True
                        try:
                            competitor.complete_turn('two', 'Example', ended_at=50)
                        except sqlite3.OperationalError as error:
                            if 'locked' not in str(error):
                                raise
                            blocked.append(True)
                    return rows

            self.assertTrue(RacingAdoption(first, tally).adopt_running_baseline_from_tally())
            expected = 120 if blocked else 130
            self.assertEqual(read_tally_snapshot(tally)['running']['working_seconds'], expected)
            if blocked:
                self.assertTrue(competitor.complete_turn('two', 'Example', ended_at=50))
            self.assertEqual(read_tally_snapshot(tally)['running']['working_seconds'], 130)

    def test_baseline_adoption_refuses_unprojected_completed_turn(self):
        from tally_writer import TallyWriter
        from tally_snapshot import read_tally_snapshot

        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            tally.write_text('## Current response\nWorking Time Running tally: 100 seconds\n', encoding='utf-8')
            def fail_replace(_source, _destination):
                raise OSError('injected file failure')
            writer = TallyWriter(db, tally, replace=fail_replace)
            writer.migrate()
            before = db.execute("SELECT value FROM tally_meta WHERE key='baseline'").fetchone()[0]
            writer.record_turn_started('pending', started_at=10)
            self.assertFalse(writer.complete_turn('pending', 'Example', ended_at=30))
            self.assertFalse(writer.adopt_running_baseline_from_tally())
            self.assertEqual(db.execute("SELECT value FROM tally_meta WHERE key='baseline_sequence'").fetchone()[0], '0')
            self.assertEqual(db.execute("SELECT value FROM tally_meta WHERE key='baseline'").fetchone()[0], before)
            recovered = TallyWriter(db, tally)
            recovered.migrate()
            self.assertEqual(read_tally_snapshot(tally)['running']['working_seconds'], 120)

    def test_missing_duration_count_is_numeric_only_from_current_block(self):
        from tally_snapshot import parse_tally_snapshot

        for value, expected in [('8', 8), ('0', 0), ('unavailable', None), ('-1', None), ('1.5', None)]:
            with self.subTest(value=value):
                text = ('## Response — old\nWorking Time Unavailable Responses: 99\n'
                        '## Current response\nWorking Time Unavailable Responses: ' + value + '\n'
                        '## Notes\nWorking Time Unavailable Responses: 77\n')
                self.assertEqual(parse_tally_snapshot(text)['working_time_missing_responses'], expected)
