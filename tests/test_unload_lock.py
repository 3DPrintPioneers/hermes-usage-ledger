"""Teardown locking: unloading must not release the sqlite handle under a foreign lock.

Hermes runs plugin hook callbacks on per-call daemon worker threads
(``hermes-hook-*``). ``_close_resources`` is registered through ``ctx.on_unload``
and used to close the shared ``sqlite3.Connection`` while those workers were
still inside ``execute()`` — the handle was freed underneath them and the whole
gateway process died with SIGSEGV (issue #1).

The invariant guarded here: teardown has to release the connection while holding
the same per-instance lock every DB method uses.
"""
import importlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class _LockRecorder:
    """``RLock`` facade that counts how many times it is currently held.

    ``threading.RLock`` does not expose ``locked()``, so the tests observe the
    hold count instead of probing the native lock.
    """

    def __init__(self):
        self._real = threading.RLock()
        self.held = 0

    def acquire(self, *args, **kwargs):
        result = self._real.acquire(*args, **kwargs)
        self.held += 1
        return result

    def release(self):
        self._real.release()
        self.held -= 1

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc_info):
        self.release()
        return False


class _CloseProbe:
    """Delegates to a real sqlite3 connection and records the DB lock state at close()."""

    def __init__(self, connection, lock, label, observed):
        self.connection = connection
        self.lock = lock
        self.label = label
        self.observed = observed

    def close(self):
        self.observed.append((self.label, self.lock.held > 0))
        return self.connection.close()

    def __getattr__(self, name):
        return getattr(self.connection, name)


def _load_plugin_package():
    """Import the plugin package under a valid module name (its directory is ``usage-ledger``)."""
    tmp = tempfile.mkdtemp()
    link = Path(tmp) / "usage_ledger"
    link.symlink_to(PROJECT_ROOT, target_is_directory=True)
    sys.path.insert(0, tmp)
    try:
        return importlib.import_module("usage_ledger")
    finally:
        sys.path.remove(tmp)


class UnloadLockTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin_package()

    def tearDown(self):
        setattr(self.plugin, "_ledger", None)
        setattr(self.plugin, "_tally_writer", None)

    def _install_resources(self):
        from ledger import UsageLedger
        from tally_writer import TallyWriter

        ledger = UsageLedger(sqlite3.connect(":memory:"))
        ledger.migrate()
        tally = TallyWriter(
            sqlite3.connect(":memory:"), Path(tempfile.mkdtemp()) / "tally.md"
        )
        tally.migrate()
        setattr(self.plugin, "_ledger", ledger)
        setattr(self.plugin, "_tally_writer", tally)
        return ledger, tally

    def test_close_resources_releases_the_connection_under_the_db_lock(self):
        """Teardown must hold the resource's own lock while closing it."""
        ledger, tally = self._install_resources()
        observed = []
        ledger_lock = _LockRecorder()
        tally_lock = _LockRecorder()
        ledger._lock = ledger_lock
        tally._lock = tally_lock
        ledger.connection = _CloseProbe(ledger.connection, ledger_lock, "ledger", observed)
        tally.connection = _CloseProbe(tally.connection, tally_lock, "tally", observed)

        self.plugin._close_resources()

        self.assertEqual([label for label, _ in observed], ["ledger", "tally"])
        for label, locked in observed:
            self.assertTrue(
                locked,
                "%s released its connection without holding its DB lock" % label,
            )

    def test_unload_waits_for_an_in_flight_db_call(self):
        """Teardown must not close the connection while another thread holds the DB lock."""
        ledger, _ = self._install_resources()
        ledger_lock = _LockRecorder()
        ledger._lock = ledger_lock
        observed = []
        ledger.connection = _CloseProbe(ledger.connection, ledger_lock, "ledger", observed)

        entered = threading.Event()
        release = threading.Event()

        def holder():
            with ledger_lock:
                entered.set()
                release.wait(5)

        holder_thread = threading.Thread(target=holder)
        holder_thread.start()
        try:
            self.assertTrue(entered.wait(5), "holder never took the DB lock")

            teardown = threading.Thread(target=self.plugin._close_resources)
            teardown.start()
            try:
                time.sleep(0.3)
                self.assertEqual(
                    observed,
                    [],
                    "teardown closed the connection while the DB lock was held elsewhere",
                )
            finally:
                release.set()
                holder_thread.join(timeout=5)
                teardown.join(timeout=5)
        finally:
            release.set()

        self.assertEqual([label for label, _ in observed], ["ledger"])
        self.assertEqual(ledger_lock.held, 0)


if __name__ == "__main__":
    unittest.main()
