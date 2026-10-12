# Changelog

All notable changes to this plugin are documented here.

## [0.4.4] - Windows and macOS beta

### Fixed

- Read legacy profile-global numeric tally headers/token labels before history sections, preserving unavailable values and explicit missing-work counts.
- Derive expanded Activity tokens only from explicitly available Canonical and reasoning totals.
- Add a `finalize_tally` setting (default true) so installations with another global tally writer can disable automatic finalization without disabling numeric observations or the display.
- Preserve the reader's normalized missing-work count when adopting a legacy root tally, ignoring conflicting history fields.
- Refuse stale automatic recovery/projection when another tally owner changed running values or missing-work counts; an explicit baseline reconciliation is required. Store only numeric projection provenance.
- Preserve numeric pending-projection checkpoints before journal mutations, so crash-after-replace recovery and incoming completions/observations/starts do not depend on successful file rewriting. An unwritable projection does not disable an already-validated numeric journal.
- Accept finite scientific-notation measurements in the shared numeric reader, rejecting overflow, nonzero underflow, and malformed partial values; preserve exact numeric round-trip compatibility with journal provenance.
- Add the allowlisted `usage-ledger` numeric command and Desktop SDK fallback for startup-unmounted REST routes; preserve confirmation before destructive clear and do not fall back on authentication/network failures.

### Release scope

- Native macOS validation: macOS 14.3.1, Apple Silicon/arm64. Intel Mac native UI coverage is not established.
- Existing Windows native layout/default collection behavior is preserved. Fresh cross-platform automated CI is linked in the release notes; it is not native UI evidence for untested hosts.
- No Hermes core changes, provider test calls, external telemetry, machine-to-machine accounting transfer, or automatic process restart.
- Existing install-scanner cautions remain disclosed. Catalog metadata/pins change only after a maintainer merges the separately reviewed update PR.

## [0.4.3] - Windows beta

### Fixed

- Bound the native Usage popover with viewport-aware inline sizing rather than a Tailwind width class absent from the installed app's compiled stylesheet.
- Wrap metric values and provenance text, and allow vertical scrolling on short windows without clipping running totals.
- Align submission metadata and CI dependency setup with the public release candidate.
- Serialize concurrent ledger callbacks and resource initialization; a ledger failure no longer suppresses independent tally staging.
- Preserve unmanaged tally sections after the current-response block.
- Treat backward clock intervals as unavailable rather than fabricating zero working time.
- Migrate observed-ledger references to per-profile HMAC identities while preserving observation values and deduplication. Backend upgrades require older collectors to be stopped first; renderer-only hot reload remains separate.

### Release scope

- Windows Desktop beta. Automated portability checks do not establish live macOS or Linux Desktop support.
- Local-only, with no provider calls or credentials required by the plugin.

## [0.4.2] - Local repair

### Fixed

- Running working time now adds measured durations to the preserved numeric baseline even when historical turns lack a start timestamp.
- The cumulative value is explicitly marked incomplete when measurements are missing; unknown legacy baselines remain unavailable.
- Existing applied history is reprojected once without resetting the baseline, deleting rows, or guessing missing durations.
- The native popover labels incomplete running working time and reports missing-response counts; the compact status uses `Work*`.

## [0.4.1]

### Added

- Native `Refresh now` action in the Usage popover.
- Immediate parallel refresh of tally and diagnostics queries.
- Disabled/loading state while a manual refresh is in progress.
- Publication documentation, security policy, contribution guidance, and CI.

## [0.4.0]

### Added

- Read-only diagnostics for tally and observed-ledger state.
- Confirmation-protected clearing of observed-request rows.
- Reopen/recovery and duplicate-finalization lifecycle coverage.
- Native diagnostics and safe-operation controls in the Usage popover.

## [0.3.0]

### Added

- Native statusline metric selection with persistent per-plugin visibility
  preferences.
