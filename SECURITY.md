# Security Policy

## Supported versions

| Version | Support |
| --- | --- |
| 0.4.x | Current development and release line |

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories
once this repository is public. Do not open a public issue for a suspected
vulnerability.

Never include prompt text, assistant text, tool arguments, provider payloads,
credentials, API keys, access tokens, passwords, or local database files in a
report. Describe the smallest reproducible behavior and redact identifying
values.

## Security model

- The Python hooks run in the Hermes process. Enabling the plugin means
  trusting the package code.
- The plugin writes only its local numeric tally state and its content-free
  observed-request ledger. It does not send telemetry or make provider API
  calls.
- Raw prompts, responses, tool data, credentials, and provider payloads are
  not stored.
- The destructive clear action requires both native confirmation and an
  explicit backend confirmation flag, and is limited to observed-request rows.
- Disabling, updating, or uninstalling the plugin must not delete the ledger or
  confirmed tally state.

Users should review the pinned source commit before enabling the plugin. Store
or catalog inclusion is a review and discovery signal, not a sandbox or a
complete security audit.
