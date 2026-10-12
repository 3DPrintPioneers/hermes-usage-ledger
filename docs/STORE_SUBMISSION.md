# Catalog update checklist

## Package identity

- Name: `usage-ledger`
- Current package version: `0.4.4`
- Support: Windows and macOS beta; native macOS tested on macOS 14.3.1 Apple Silicon/arm64. Intel Mac native UI remains unverified.
- Maintainer: `3DPrintPioneers`
- Repository: https://github.com/3DPrintPioneers/hermes-usage-ledger
- Previous catalog pin: `a86ab64a4cb3ce7872fb2dfc7242484fc2b8c20b` (0.4.3).
- Hermes requirement: `>=0.21.5` with Desktop Plugin SDK.
- Package: Python hooks, one public numeric command, scoped dashboard API, native Desktop statusline contribution.
- Runtime dependencies: none beyond Hermes and the Desktop plugin SDK.
- External credentials, telemetry, or provider calls: none.

## Release gates

The release and catalog PR must link the new exact source SHA, independent
review, and fresh Ubuntu/Windows/macOS CI for Python 3.11 and 3.12. Automated
CI is not native UI validation. Native Mac evidence covers only Apple Silicon;
existing Windows native behavior/layout and default finalizer are retained.
The isolated native/lifecycle validation is described in [MACOS.md](MACOS.md).
Do not publish real accounting fixtures, databases, private screenshots,
credentials, or raw hook payloads as evidence.

## Distribution route

Updating the existing entry is a maintainer-reviewed PR changing only
`plugin-catalog/usage-ledger.yaml` in
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
The former separate `hermes-plugin-index` repository is not the route.

Follow the current [submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission)
and [catalog schema](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md).
Use the full reviewed 40-character source SHA, `version: "0.4.4"`,
`tier: community`, `category: desktop`, `platforms: [windows, macos]`,
the existing Hermes floor, a README URL pinned to that SHA, and these hooks:
`pre_llm_call`, `post_api_request`, `post_llm_call`, `on_session_finalize`.
The manifest also declares the `usage-ledger` command. Opening a PR is not
catalog admission; maintainers must review and merge it. Check the live feed
before claiming users can install the new version by catalog name.

## Trust disclosure

The install/admission scanner reports the literal inline-code counting regex
in `tally_writer.py` and dependency installation in `.github/workflows/ci.yml`.
They must be disclosed for review: the regex counts text, not shell execution;
the CI command installs isolated test dependencies. Do not represent a caution
as a clean scan or bypass a dangerous finding.

Enabling the plugin executes Python hooks in-process. The default automatic
finalizer counts assistant text only in memory, stores numeric state, and
atomically replaces the managed `HERMES_HOME/tally.md` block. When another
updater owns that file, disable `finalize_tally` before activation. Only numeric
state and keyed opaque request metadata persist in plugin data; prompts and
responses are not stored. Reads use the intended profile's tally/databases.
There is no external telemetry. The allowlisted public command duplicates the
numeric REST API for startup-route 404s; clear requires explicit confirmation
on both paths. Disable/update/uninstall preserve data. Older request-identity
schemas require quiescing old writers before migration; see the README.
