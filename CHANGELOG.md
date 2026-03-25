# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project adheres to Semantic Versioning.

## [0.1.0] - 2026-03-24

### Added

- Open-source repository bootstrap for `geo-llms-toolkit`.
- Current production WordPress adapter under `adapters/wordpress`.
- Initial project docs:
- architecture
- roadmap
- migration plan
- adapter contract draft
- Governance/community files:
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- GitHub issue templates and issue config.
- GitHub Actions CI workflow for WordPress adapter PHP lint.
- Social links in root README.

## [0.2.0] - 2026-03-24

### Added

- Standalone CLI entrypoint `./geo` (non-WordPress usage).
- Standalone engine script `standalone/geo_toolkit.py`.
- CLI commands:
- `scan` for GEO signal/endpoint checks
- `llms` for `llms.txt` and `llms-full.txt` generation
- `all` for one-shot scan + llms generation
- Standalone usage docs at `standalone/README.md`.

### Changed

- Root `README.md` now defaults to standalone quick start, with WordPress adapter as optional path.

## [0.3.0] - 2026-03-25

### Added

- New standalone CLI command `monitor`:
- keyword-file driven competitor monitoring
- optional competitor auto-discovery from SERP
- brand vs non-brand keyword separation
- competitor scoring and tiering (`direct` / `potential` / `peripheral`)
- prioritized action output (`P0` / `P1` / `P2`)
- run snapshot persistence under `.geo-history/`
- Added `examples/keywords.txt` for monitor command quick start.

### Changed

- Standalone docs and root README now include competitor monitor usage.
- Standalone CLI version and default user-agent bumped to `0.3.0`.

## [0.4.0] - 2026-03-25

### Added

- New standalone CLI command `outreach`:
- build outreach plans from `geo monitor --format json` results
- generate `outreach-prospects.csv` for automation pipelines
- generate `outreach-plan.json` for programmatic integration
- generate `outreach-sequences.md` with template emails
- include domain-level filtering for non-outreach domains and custom exclusions

### Changed

- Root and standalone README updated with monitor -> outreach workflow examples.
- Standalone CLI description updated to include outreach planning.

## [0.5.0] - 2026-03-25

### Added

- `geo outreach plan/run/status` workflow:
- `plan`: generates outreach artifacts and persistent campaign file
- `run`: executes campaign with provider options (`dry-run` / `webhook` / `command`)
- `status`: renders campaign run status summary
- Campaign state file: `outreach-campaign.json` with prospect-level status/attempt logs
- Cross-run dedupe state: `.geo-history/outreach-state.json` for only-new strategy
- Cooldown window control for repeat outreach suppression
- Provider integration hooks for webhook and command execution

### Changed

- `geo outreach` now defaults to `plan` action for backward compatibility.
- Standalone CLI version and default user-agent bumped to `0.5.0`.

## [0.6.0] - 2026-03-25

### Added

- `geo monitor` supports external weights config via `--weights-file`.
- `geo monitor-diff` command to compare two monitor snapshots (score/action deltas).
- `geo outreach verify` action for backlink win checks and follow-up due marking.
- `geo outreach update` action for manual status transitions (`replied`/`won`/`lost`, etc.).
- Optional contact enrichment in outreach planning via `--enrich-contacts`.

### Changed

- Outreach campaign status now tracks richer lifecycle states:
- `queued`, `sent`, `followup_due`, `replied`, `won`, `lost`, `failed`, `skipped`.
- Standalone CLI version and default user-agent bumped to `0.6.0`.
