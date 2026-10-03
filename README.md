# Usage Ledger

A local Hermes Desktop plugin that adds one **Usage** control to the existing left status-bar group. The popover reads structured values from the active profile’s `tally.md`; it does not add a sidebar, widget, or second status bar.

## Windows beta 0.4.3

Maintainer: [3DPrintPioneers](https://github.com/3DPrintPioneers). Requires Hermes
0.21.5 or newer with the Desktop Plugin SDK. The repository is
[3DPrintPioneers/hermes-usage-ledger](https://github.com/3DPrintPioneers/hermes-usage-ledger).

## Install the pinned release

Use the exact 40-character commit SHA recorded in the release notes. In this
example, replace `<release-commit-sha>` with that SHA, not a branch or tag:

```bash
hermes plugins install 3DPrintPioneers/hermes-usage-ledger --ref <release-commit-sha>
hermes plugins enable usage-ledger
```

The unified package has two activation surfaces: enable the Python plugin with
`hermes plugins enable`, then enable the **Usage Ledger** Desktop renderer in
Desktop **Settings → Plugins**. A bare `hermes plugins install usage-ledger`
will work only after the plugin is accepted into the Hermes discovery index or
catalog.

## Support status

- Windows: the backend and native Desktop display have been exercised locally.
  Recovery, reopening, and data-retention tests use isolated temporary profiles;
  the active user's app is not restarted or cleared for release validation.
- macOS/Linux: CI targets automated portability checks; complete live Desktop
  verification before advertising those platforms.

## What the control shows

The popover shows **Current** and **Running** values for input, output, cache-read, cache-write, reasoning, **Canonical tokens**, and **Activity tokens (expanded)**; calls; thinking and working durations; words; characters; and coding characters.

- Missing or unsupported values render as `N/A`; an explicit measured `0` remains `0`.
- The renderer polls its read-only `/tally` endpoint every **30 seconds**; **Refresh now** in the popover refetches the tally and diagnostics immediately.
- `/tally` reads only labeled numeric fields from the latest `## Current response` block. A later unmanaged section cannot override it.
- `tally.md` remains separate from the observed provider-request ledger. `/summary` never blends those scopes.

## Statusline selection

Clicking **Usage** opens every metric. The first **Show** column contains one native checkbox per metric: checked rows appear in the compact left status-bar summary, while unchecked rows remain available in the popover but are hidden from that summary. The Usage control itself remains available when all rows are unchecked.

The selection is stored as an ordered list of metric keys in the plugin-scoped Desktop storage, defaults to all rows checked, updates immediately, and survives plugin reloads and Hermes restarts. It does not change collection, tally values, the observed-request ledger, model selection, or status-bar placement.

## Phase D safe operations

The Usage popover includes a compact Diagnostics section showing the active-profile tally scope, 30-second refresh interval, tally-state schema/baseline/completion state, observed-ledger schema/count, and the number of selected statusline metrics. Diagnostics use read-only database connections and render unavailable state as `N/A`.

The **Clear observed ledger** action is destructive only after the native confirmation dialog is accepted. Its backend endpoint also requires an explicit confirmation flag, clears only observed-request rows, preserves the ledger schema and all `tally.md`/finalizer/statusline data, and is read back through `/summary` before the dialog reports success. Disabling, updating, or uninstalling the plugin does not invoke this action or delete its data.

## Phase C automatic local tally finalizer

For each normal, successful final response that Hermes emits through `post_llm_call`, the backend plugin:

1. records an opaque start reference at `pre_llm_call`;
2. stages allowed request metrics from `post_api_request` using an opaque logical-request reference;
3. counts the final assistant text **only in memory** at `post_llm_call`, excluding a terminal Hermes usage footer; and
4. persists only numeric word, character, code-character, timing, call, and source-backed token values, then atomically replaces the managed current-response block in `tally.md`.

`post_api_request` can occur for tool-loop/intermediate model calls, so it never writes the human-visible tally itself. Finalization is deduplicated by a per-profile HMAC of Hermes’s stable turn ID; staged calls are deduplicated by a per-profile HMAC of Hermes’s logical API request ID. The raw IDs and response text are not written to disk.

Hermes’s supported plugin contract requires hook callbacks to accept `**kwargs`. The callbacks immediately copy only an allowlisted primitive subset, clear the payload, and never log, serialize, hash, cache, or persist prompt text, assistant text, tool arguments/results, credentials, raw provider payloads, URLs, headers, or response objects. The final assistant response is held only long enough to calculate numeric text counts.

### Availability rules

- Individual provider usage buckets are counted only when every staged request for that finalized turn supplies a positive finite integer. Missing, zero-normalized, malformed, or partial buckets are `N/A`, never synthetic zeroes.
- **Canonical tokens** are the derived sum of input + cache-read + cache-write + output, only when every component is observed. They are not represented as a provider-supplied `total_tokens` measurement.
- **Activity tokens (expanded)** are Canonical tokens + reasoning, only when every component is observed. This intentionally counts reasoning as additional activity even when a provider reports it as an output subcategory; it is shown separately rather than conflated with Canonical tokens.
- Running Canonical and Activity totals are derived from their respective running component buckets, so the two totals remain intelligible when the latest response has an unavailable bucket.
- **Thinking time** is the sum of observed provider API durations only when every staged request reports one. It is not private model reasoning time.
- **Working time** is a local wall-clock proxy from `pre_llm_call` to finalization.
- Running working time adds measured durations to the confirmed numeric baseline. Missing durations do not block later additions and are never estimated. If any duration is unavailable, the popover marks the running value **incomplete**, explains its scope and missing-response count, and the status bar uses `Work*`. An unknown legacy working-time baseline still renders `N/A`.
- A fresh tally begins from a known local zero baseline. When adopting an existing legacy tally, unavailable legacy values remain `N/A` rather than being mixed with a partial post-install total.
- A user-directed **running-total baseline** adopts the complete Running column from the latest canonical `## Current response` block, including any user-corrected values. Future observed provider values add from that exact baseline; current unavailable buckets remain `N/A`, and the adoption is explicitly recorded in the tally rather than presented as a reconstructed billing total.

### Upgrade compatibility

**0.4.3 backend upgrades require a quiescent restart.** Finish active agent
turns and stop all older Hermes processes using this profile before the new
backend or dashboard API loads. Do not migrate the request ledger while cached
0.4.1/0.4.2 collectors can still write; mixed-version request collectors are not
supported. The plugin never restarts the app automatically. Renderer-only
layout updates can be hot-reloaded separately without migrating either database.

The earlier working-only 0.4.2 repair supported ordinary cached 0.4.1 tally
completion callbacks. That narrower compatibility does **not** cover the 0.4.3
request-ledger identity migration.

Explicit running-baseline adoption is **not** a hook or exposed API route.
Perform any future user-approved baseline correction only through a freshly
imported current writer, never a cached older writer. Adoption locks the source
reads and baseline update together, refuses unprojected pending history, then
durably replays the projection if file replacement fails.

### Durability and limits

The numeric source of truth is `plugin-data/usage-ledger/tally-state.db`, not incremental edits to `tally.md`. A finalized turn is first committed as a pending SQLite row. The plugin serializes the full projection with `BEGIN IMMEDIATE`, writes and fsyncs a same-directory temporary file, replaces `tally.md` atomically, then marks the row applied. Pending rows replay on the next finalization boundary or restart. Concurrent writer connections cannot leave a torn projection.

This is exactly-once for duplicate hook delivery, retries sharing Hermes’s logical API request ID, plugin reloads, late pending writes, and restart recovery. It cannot recover a response if Hermes terminates before it emits the observer hook; it deliberately does **not** scan transcripts, prompts, or provider payloads to backfill such an event.

## Local observed-request ledger

`plugin-data/usage-ledger/ledger.db` is a separate post-install provider-observation ledger. It stores only per-profile HMAC request/session/turn references, bounded safe provider/model labels, positive input/output observations, and an observation timestamp. A random local identity key remains inside its SQLite metadata, not in the install directory or public source. Schema 2 transactionally wraps legacy SHA-256 references in that keyed scheme without changing usage values, timestamps, row counts, or deduplication. This migration does not scrub old backups or SQLite free pages. Keep backups private. It never promotes Hermes’s normalized `total_tokens` value as source-backed; the displayed Canonical and Activity totals are explicitly derived from observed buckets. Existing observations are preserved; disabling or uninstalling the package does not delete either database.

## Tests

```bash
hermes plugins doctor . --ci
python -m unittest discover -s tests -v
node --experimental-vm-modules --test tests/desktop-plugin.test.mjs
```

The real-hook test uses an isolated temporary `HERMES_HOME`; it makes no provider call and never touches the installed profile.
