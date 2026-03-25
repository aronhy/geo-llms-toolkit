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
