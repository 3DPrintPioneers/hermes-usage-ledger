# macOS setup — Usage Ledger 0.4.4

Usage Ledger supports Windows and macOS as a beta. The native macOS validation
was performed on macOS 14.3.1, Apple Silicon/arm64, with the installed Hermes
Desktop application and an isolated Hermes test runtime. Intel Mac native
validation is not established; automated macOS CI does not extend that claim.
See the release notes for the exact source SHA and cross-platform CI run.

## Installation and activation

Install the exact 40-character source SHA from the release notes, using the
commands in the [README](../README.md). The catalog stays on its previously
reviewed pin until Hermes maintainers merge the separate update pull request.
Do not accept an unexpected scanner warning or disable scanning.

The package belongs under the intended profile's `HERMES_HOME/plugins/usage-ledger`.
No core source edits are required. With the Agent half still disabled,
configure the writer ownership as described below. Then enable the Agent half
through Capabilities → Plugins, and enable the Desktop half in the same row. The
Usage control appears in the existing bottom-left status-bar group. No
alternate sidebar or popup layout is introduced.

For an existing profile-global tally updater, turn **Automatically finalize
tally.md** off in the plugin's settings before enabling collection. This is
the tested Mac configuration: the existing updater remains the only writer,
while the plugin reads current/running values and records content-free numeric
request observations separately. Do not enable automatic finalization while
another writer owns the file. The default remains on for ordinary new
installations, preserving the existing Windows behavior.

The setting applies at registration, not retroactively to cached callbacks.
Before changing ownership, finish agent turns and quiesce all profile writers;
disable the Agent half, change settings, then re-enable it. If another writer
changed the running values or missing-work count, automatic finalization
refuses stale recovery/projection. Keep it off until an explicitly approved
baseline reconciliation audits the existing state, especially pending rows.
Do not delete accounting data or run competing writers to bypass this gate.

When upgrading a 0.4.1/0.4.2 backend, finish agent turns and stop all older
processes writing the same profile before installation: the 0.4.3-and-newer
observed ledger uses schema 2. See the README's upgrade caution. The plugin
does not restart processes automatically or transfer totals between machines.

## Compatibility changes

- Recognize the older root-level global tally before history sections, along
  with its `Tokens Input` and decorated `Token Running Total Input` labels.
  This prevents an older history heading from hiding current/running data.
- Derive Activity tokens only when Canonical and reasoning values are both
  present; preserve the explicitly recorded unavailable-working-time count.
- Offer the `finalize_tally` boolean through declared plugin settings. When
  off, no tally writer is opened or invoked; observations stay independent
  of the display's tally source.
- Prefer the scoped REST API. If a route returns 404 because the running
  backend mounted its REST table before installation, fall back to the public
  `command.dispatch` SDK and registered `usage-ledger` command. Authentication
  and network failures are not a reason to bypass REST. The command accepts
  only allowlisted numeric actions; clear still requires explicit confirmation.

Every metric remains in the Current/Running popover. Native Show checkboxes
control only the compact summary. All-unchecked keeps the Usage button;
scheduled refresh is 30 seconds, and Refresh now refetches immediately.
Unavailable values remain N/A. Thinking time in automatic collection mode
means API durations, not private inference time. An external updater's
unavailable private-thinking measurements remain unavailable.

## Validation and data preservation

Native isolated tests checked popup bounds, all 13 metric rows, native
checkboxes, all-hidden behavior, preference persistence across reopen,
30-second polling, manual refresh, and installation into an already-running
backend. Synthetic hook events and disposable fixture lifecycle checks tested
accounting, deduplication, recovery, and retention across disable, local
update, uninstall, and reinstall. No provider calls or real accounting data
were used as fixtures. Manual destructive clear was not performed on the
active profile; automated confirmation tests use disposable fixtures only.

No Windows accounting data is imported. Request references remain local and
keyed; response text is not persisted. Disable/uninstall do not clear either
database or the tally. CI validates backend/renderer portability, not native
Desktop on every OS/architecture. Linux native support is not advertised.

The install scanner reports the existing literal inline-code regex and CI
dependency-installation cautions. Disclose and review them; do not describe
this as an unqualified clean scan or force past a dangerous verdict.
