import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_hooks():
    name = 'usage_ledger_release_fixture'
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseIntegrityTests(unittest.TestCase):
    def test_identity_migration_rolls_back_partial_rekey_failure(self):
        from ledger import UsageLedger

        class FailingConnection(sqlite3.Connection):
            fail = False
            def executemany(self, query, values):
                if self.fail and query.startswith('UPDATE observed_requests'):
                    rows = list(values)
                    if rows:
                        self.execute(query, rows[0])
                    raise sqlite3.OperationalError('injected rekey failure')
                return super().executemany(query, values)

        with closing(sqlite3.connect(':memory:', factory=FailingConnection)) as connection:
            connection.execute('CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            connection.execute("INSERT INTO ledger_meta VALUES ('schema_version', '1')")
            connection.execute('CREATE TABLE observed_requests (event_id TEXT PRIMARY KEY, session_ref TEXT, turn_ref TEXT, provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, observed_at INTEGER NOT NULL)')
            connection.execute("INSERT INTO observed_requests VALUES ('legacy', NULL, NULL, NULL, NULL, 12, 3, NULL, 100)")
            connection.commit()
            before = list(connection.iterdump())
            connection.fail = True
            ledger = UsageLedger(connection)
            with self.assertRaisesRegex(sqlite3.OperationalError, 'rekey'):
                ledger.migrate()
            self.assertEqual(list(connection.iterdump()), before)
            connection.fail = False
            ledger.migrate()
            self.assertEqual(ledger.summary()['observed_requests'], 1)
            self.assertEqual(ledger.summary()['input_tokens'], 12)

    def test_two_migration_connections_share_one_identity_key(self):
        from ledger import UsageLedger

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'ledger.db'
            def migrate_and_record(_index):
                with closing(sqlite3.connect(database, timeout=5)) as connection:
                    ledger = UsageLedger(connection)
                    ledger.migrate()
                    ledger.record_observed_request({'api_request_id': 'same-request', 'usage': {'input_tokens': 12}})
                    return connection.execute("SELECT value FROM ledger_meta WHERE key='identity_key'").fetchone()[0]
            with ThreadPoolExecutor(max_workers=2) as pool:
                keys = list(pool.map(migrate_and_record, range(2)))
            self.assertEqual(keys[0], keys[1])
            with closing(sqlite3.connect(database)) as connection:
                ledger = UsageLedger(connection)
                ledger.migrate()
                self.assertEqual(ledger.summary()['observed_requests'], 1)

    def test_failed_resource_migration_does_not_cache_an_uninitialized_object(self):
        from ledger import UsageLedger

        hooks = load_hooks()
        connections = []

        class RetryLedger(UsageLedger):
            first = True
            def migrate(self):
                if self.first:
                    type(self).first = False
                    raise sqlite3.OperationalError('injected cold migration failure')
                return super().migrate()

        def open_connection(_plugin, _filename):
            connection = sqlite3.connect(':memory:')
            connections.append(connection)
            return connection

        try:
            with patch.object(hooks, 'UsageLedger', RetryLedger), patch('plugins.plugin_storage.plugin_db', side_effect=open_connection):
                with self.assertRaises(sqlite3.OperationalError):
                    hooks._get_ledger()
                self.assertIsNone(hooks._ledger)
                ledger = hooks._get_ledger()
                self.assertTrue(ledger.record_observed_request({'api_request_id': 'retry', 'usage': {'input_tokens': 1}}))
        finally:
            hooks._close_resources()
            for connection in connections:
                connection.close()

    def test_legacy_reference_migration_preserves_usage_and_deduplication(self):
        import hashlib
        from ledger import UsageLedger

        with closing(sqlite3.connect(':memory:')) as connection:
            connection.execute('CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            connection.execute("INSERT INTO ledger_meta VALUES ('schema_version', '1')")
            connection.execute('CREATE TABLE observed_requests (event_id TEXT PRIMARY KEY, session_ref TEXT, turn_ref TEXT, provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, observed_at INTEGER NOT NULL)')
            old_refs = tuple(hashlib.sha256(value.encode()).hexdigest() for value in ('same-request', 'same-session', 'same-turn'))
            connection.execute('INSERT INTO observed_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (*old_refs, 'fixture', 'fixture', 12, 3, None, 100))
            connection.commit()
            ledger = UsageLedger(connection)
            ledger.migrate()
            self.assertEqual(ledger.summary(), {'observed_requests': 1, 'input_tokens': 12, 'output_tokens': 3, 'total_tokens': None})
            self.assertFalse(ledger.record_observed_request({'api_request_id': 'same-request', 'session_id': 'same-session', 'turn_id': 'same-turn', 'usage': {'input_tokens': 12, 'output_tokens': 3}}))
            row = connection.execute('SELECT event_id, session_ref, turn_ref, observed_at FROM observed_requests').fetchone()
            for old, migrated in zip(old_refs, row[:3]):
                self.assertNotEqual(old, migrated)
            self.assertEqual(row[3], 100)
            dump = list(connection.iterdump())
            ledger.migrate()
            self.assertEqual(list(connection.iterdump()), dump)

    def test_request_references_are_profile_specific_and_survive_reopen(self):
        from ledger import UsageLedger

        with closing(sqlite3.connect(':memory:')) as first, closing(sqlite3.connect(':memory:')) as second:
            references = []
            payload = {'api_request_id': 'same-request', 'session_id': 'same-session', 'turn_id': 'same-turn', 'usage': {'input_tokens': 2}}
            for connection in (first, second):
                ledger = UsageLedger(connection)
                ledger.migrate()
                self.assertTrue(ledger.record_observed_request(payload))
                references.append(connection.execute('SELECT event_id, session_ref, turn_ref FROM observed_requests').fetchone())
                reopened = UsageLedger(connection)
                reopened.migrate()
                self.assertFalse(reopened.record_observed_request(payload))
                self.assertEqual(reopened.summary()['observed_requests'], 1)
            for left, right in zip(*references):
                self.assertNotEqual(left, right)

    def test_finalization_preserves_unmanaged_sections_after_current_block(self):
        from tally_writer import TallyWriter
        from tally_snapshot import read_tally_snapshot

        suffix = '## Notes\nPersonal note with a literal ## Current response mention.\n\n## Other history\nKeep this section intact.\n'
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as connection:
            tally = Path(directory) / 'tally.md'
            tally.write_text('# Tally\n\n## Current response\nWords Running tally: 10\n\n' + suffix, encoding='utf-8')
            writer = TallyWriter(connection, tally)
            writer.migrate()
            self.assertTrue(writer.complete_turn('one', 'fixture', ended_at=100))
            self.assertTrue(tally.read_text(encoding='utf-8').endswith(suffix))
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 11)
            self.assertTrue(writer.complete_turn('two', 'fixture', ended_at=101))
            contents = tally.read_text(encoding='utf-8')
            self.assertTrue(contents.endswith(suffix))
            self.assertEqual(contents.count('## Notes'), 1)
            self.assertEqual(read_tally_snapshot(tally)['running']['words'], 12)

    def test_invalid_clock_intervals_are_unavailable_not_measured_zero(self):
        from tally_writer import TallyWriter
        from tally_snapshot import read_tally_snapshot

        for start, end, expected in [(100, 90, None), (100, 100, 0), (100, 105, 5)]:
            with self.subTest(start=start, end=end), tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as connection:
                tally = Path(directory) / 'tally.md'
                writer = TallyWriter(connection, tally)
                writer.migrate()
                writer.record_turn_started('turn', started_at=start)
                writer.complete_turn('turn', 'fixture', ended_at=end)
                snapshot = read_tally_snapshot(tally)
                self.assertEqual(snapshot['current']['working_seconds'], expected)
                self.assertEqual(snapshot['working_time_missing_responses'], int(expected is None))
                self.assertEqual(snapshot['running']['working_seconds'], expected or 0)

    def test_concurrent_first_use_creates_only_one_ledger(self):
        import threading
        import time

        hooks = load_hooks()
        calls = []
        connections = []
        barrier = threading.Barrier(8)

        def open_connection(_plugin, _filename):
            connection = sqlite3.connect(':memory:', check_same_thread=False)
            connections.append(connection)
            calls.append(1)
            time.sleep(0.025)
            return connection

        def get_ledger(_index):
            barrier.wait(timeout=5)
            return hooks._get_ledger()

        try:
            with patch('plugins.plugin_storage.plugin_db', side_effect=open_connection):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    instances = list(pool.map(get_ledger, range(8)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len({id(item) for item in instances}), 1)
        finally:
            hooks._close_resources()
            for connection in connections:
                connection.close()

    def test_ledger_failure_does_not_suppress_tally_staging(self):
        from tally_writer import TallyWriter
        from tally_snapshot import read_tally_snapshot

        class BrokenLedger:
            def record_observed_request(self, _payload):
                raise sqlite3.OperationalError('injected ledger failure')

        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as connection:
            tally = Path(directory) / 'tally.md'
            writer = TallyWriter(connection, tally)
            writer.migrate()
            hooks = load_hooks()
            with patch.object(hooks, '_get_ledger', return_value=BrokenLedger()), patch.object(hooks, '_get_tally_writer', return_value=writer):
                hooks.record_observed_request(api_request_id='fixture', turn_id='turn', usage={'input_tokens': 12}, api_duration=1)
                hooks.record_completed_response(turn_id='turn', assistant_response='fixture')
            self.assertEqual(read_tally_snapshot(tally)['current']['calls'], 1)
            self.assertEqual(read_tally_snapshot(tally)['current']['input_tokens'], 12)

    def test_concurrent_ledger_records_never_lose_rows_or_raise(self):
        from ledger import UsageLedger

        with tempfile.TemporaryDirectory() as directory, closing(
            sqlite3.connect(Path(directory) / 'ledger.db', check_same_thread=False)
        ) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            ledger = UsageLedger(connection)
            ledger.migrate()

            def record(index):
                try:
                    return ledger.record_observed_request({
                        'api_request_id': f'request-{index}',
                        'usage': {'input_tokens': 1},
                    })
                except Exception as error:
                    return type(error).__name__

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(record, range(300)))
            self.assertEqual(results.count(True), 300, results)
            self.assertEqual(ledger.summary()['observed_requests'], 300)
