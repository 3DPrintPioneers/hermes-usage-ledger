# Contributing

Thanks for helping improve Usage Ledger. This repository is a standalone
Hermes plugin; contributions must not modify Hermes core or add external
telemetry.

## Development requirements

- A supported Hermes installation for `hermes plugins doctor`.
- Python 3.11 or newer for the current test suite.
- Node.js 22 or newer for the renderer test.
- No provider credentials or API keys are required.
- Test dependencies are pinned in `requirements-test.txt`. CI installs an
  editable Hermes checkout at the exact reference in `.github/workflows/ci.yml`;
  a bare Python environment does not include Hermes's hook manager or FastAPI.

## Local verification

From the repository root, run:

```bash
hermes plugins doctor . --ci
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
node --experimental-vm-modules --test tests/desktop-plugin.test.mjs
python -m py_compile ledger.py dashboard/plugin_api.py __init__.py tally_snapshot.py tally_writer.py
git diff --check
```

The real-hook tests use isolated temporary Hermes homes. Do not point tests at
a personal profile, `tally.md`, `ledger.db`, or `tally-state.db`.

## Behavioral requirements

- Keep the plugin local-only and content-free.
- Never persist prompts, assistant responses, tool arguments, raw provider
  payloads, credentials, or response-content hashes.
- Preserve the distinction between tally accounting and the observed-request
  ledger.
- Keep diagnostics read-only and keep clearing confirmation-protected.
- Preserve data across disablement, updates, uninstallation, reloads, and
  restart recovery.
- Use the existing left status-bar group and native Desktop SDK controls.
- Add a behavior test before changing production behavior and verify the test
  fails for the expected reason before implementing the change.

## Pull requests

Explain the user-visible behavior, the affected storage scope, and the tests
run. Keep commits small and do not include local databases, cache files,
credentials, or generated Python bytecode.
