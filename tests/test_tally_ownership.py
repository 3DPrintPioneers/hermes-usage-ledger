import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tally_snapshot import read_tally_snapshot
from tally_writer import TallyWriter


class TallyOwnershipTests(unittest.TestCase):
    def test_explicit_legacy_handoff_preserves_normalized_missing_work_count(self):
        for history in ('', '\n## History\nWorking Time Unavailable Responses: 999\n'):
            with self.subTest(history=bool(history)), tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
                tally = Path(directory) / 'tally.md'
                writer = TallyWriter(db, tally)
                writer.migrate()
                writer.complete_turn('fixture-one', 'one', ended_at=13)
                tally.write_text('# Hermes global usage tally\nUpdated: fixture\nWords Running tally: 400.\n'
                                 'Working Time Running tally: 300 seconds measured / 5 unavailable.\n' + history, encoding='utf-8')
                self.assertTrue(writer.adopt_running_baseline_from_tally())
                self.assertEqual(read_tally_snapshot(tally)['running']['words'], 400)
                self.assertEqual(read_tally_snapshot(tally)['working_time_missing_responses'], 5)

    def test_cached_completion_recovers_written_pending_projection_before_new_row(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            with patch.object(writer, '_mark_pending_applied', side_effect=OSError('fixture crash after replace')):
                self.assertFalse(writer.complete_turn('fixture-one', 'one', ended_at=13))
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 1)
            self.assertTrue(writer.complete_turn('fixture-two', 'two words', ended_at=16))
            TallyWriter(db, tally).migrate()
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM completed_turns WHERE status = 'pending'").fetchone()[0], 0)

    def test_written_pending_state_does_not_drop_events_when_projection_is_unwritable(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            with patch.object(writer, '_mark_pending_applied', side_effect=OSError('fixture crash after replace')):
                self.assertFalse(writer.complete_turn('fixture-one', 'one', ended_at=10))
            with patch.object(writer, '_replace', side_effect=PermissionError('fixture projection unwritable')):
                self.assertFalse(writer.complete_turn('fixture-two', 'two words', ended_at=20))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM completed_turns').fetchone()[0], 2)
                self.assertTrue(writer.stage_request(api_request_id='fixture-late', turn_id='fixture-one', usage={'input_tokens':12,'output_tokens':3}, api_duration=1))
                self.assertTrue(writer.record_turn_started('fixture-three', started_at=25))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM staged_requests').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM turn_starts').fetchone()[0], 1)
            recovered = TallyWriter(db, tally)
            recovered.migrate()
            self.assertTrue(recovered.complete_turn('fixture-three', 'three more words', ended_at=30))
            snapshot = read_tally_snapshot(tally)
            self.assertEqual(snapshot['running']['words'], 6)
            self.assertEqual(snapshot['current']['working_seconds'], 5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM completed_turns WHERE status = 'pending'").fetchone()[0], 0)

    def test_reopen_keeps_journal_available_when_projection_recovery_fails(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            with patch.object(writer, '_mark_pending_applied', side_effect=OSError('fixture crash after replace')):
                self.assertFalse(writer.complete_turn('fixture-one', 'one', ended_at=10))
            def unwritable(_source, _target):
                raise PermissionError('fixture projection unwritable')
            reopened = TallyWriter(db, tally, replace=unwritable)
            reopened.migrate()
            self.assertFalse(reopened.complete_turn('fixture-two', 'two words', ended_at=20))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM completed_turns').fetchone()[0], 2)
            TallyWriter(db, tally).migrate()
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 3)

    def test_scientific_duration_roundtrip_preserves_pending_recovery(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            writer.record_turn_started('fixture-one', started_at=5)
            writer.stage_request(api_request_id='fixture-one-request', turn_id='fixture-one',
                                 usage={'input_tokens':12,'output_tokens':3,'cache_read_tokens':2,'cache_write_tokens':1,'reasoning_tokens':1},
                                 api_duration=0.0000125)
            with patch.object(writer, '_mark_pending_applied', side_effect=OSError('fixture crash after replace')):
                self.assertFalse(writer.complete_turn('fixture-one', 'one', ended_at=10))
            self.assertEqual(read_tally_snapshot(tally)['running']['thinking_seconds'], 0.0000125)
            with patch.object(writer, '_replace', side_effect=PermissionError('fixture projection unwritable')):
                self.assertFalse(writer.complete_turn('fixture-two', 'two words', ended_at=20))
            writer.recover()
            TallyWriter(db, tally).migrate()
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM completed_turns WHERE status = 'pending'").fetchone()[0], 0)

    def test_external_totals_are_not_overwritten_on_applied_or_pending_reopen(self):
        for pending in (False, True):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
                tally = Path(directory) / 'tally.md'
                tally.write_text('## Current response\nWords Running tally: 40\nWorking Time Running tally: 30 seconds\nWorking Time Unavailable Responses: 2\n', encoding='utf-8')
                writer = TallyWriter(db, tally)
                writer.migrate()
                writer.record_turn_started('fixture-one', started_at=10)
                writer.complete_turn('fixture-one', 'one', ended_at=13)
                if pending:
                    writer.record_turn_started('fixture-pending', started_at=20)
                    with patch.object(writer, '_replace', side_effect=OSError('fixture replace failure')):
                        self.assertFalse(writer.complete_turn('fixture-pending', 'one', ended_at=23))
                external = '## Current response\nWords Running tally: 400\nWorking Time Running tally: 300 seconds\nWorking Time Unavailable Responses: 5\n'
                tally.write_text(external, encoding='utf-8')
                with self.assertRaisesRegex(OSError, 'ownership'):
                    TallyWriter(db, tally).migrate()
                self.assertEqual(tally.read_text(encoding='utf-8'), external)
                self.assertEqual(read_tally_snapshot(tally)['running']['words'], 400)
                self.assertEqual(read_tally_snapshot(tally)['working_time_missing_responses'], 5)

    def test_old_matching_projection_bootstraps_guard_without_changing_totals(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            writer.complete_turn('fixture-one', 'one', ended_at=13)
            before = tally.read_text(encoding='utf-8')
            with db:
                db.execute("DELETE FROM tally_meta WHERE key = 'projection_source'")
            reopened = TallyWriter(db, tally)
            reopened.migrate()
            self.assertEqual(tally.read_text(encoding='utf-8'), before)
            reopened.complete_turn('fixture-two', 'one', ended_at=16)
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 2)

    def test_cached_writer_refuses_changed_external_totals_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            writer.complete_turn('fixture-one', 'one', ended_at=13)
            external = '## Current response\nWords Running tally: 400\nWorking Time Unavailable Responses: 5\n'
            tally.write_text(external, encoding='utf-8')
            self.assertFalse(writer.complete_turn('fixture-two', 'one', ended_at=16))
            self.assertEqual(tally.read_text(encoding='utf-8'), external)
            self.assertEqual(db.execute("SELECT status FROM completed_turns ORDER BY sequence DESC LIMIT 1").fetchone()[0], 'pending')

    def test_old_database_without_guard_refuses_changed_external_totals(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(db, tally)
            writer.migrate()
            writer.complete_turn('fixture-one', 'one', ended_at=13)
            with db:
                db.execute("DELETE FROM tally_meta WHERE key = 'projection_source'")
            external = '## Current response\nWords Running tally: 400\nWorking Time Unavailable Responses: 5\n'
            tally.write_text(external, encoding='utf-8')
            with self.assertRaisesRegex(OSError, 'ownership'):
                TallyWriter(db, tally).migrate()
            self.assertEqual(tally.read_text(encoding='utf-8'), external)
