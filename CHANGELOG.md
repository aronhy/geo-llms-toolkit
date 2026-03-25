# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project adheres to Semantic Versioning.

## [0.11.0] - 2026-03-25

### Added

- WordPress adapter now includes migrated operational workflows from standalone CLI:
- monitor pipeline in WP admin
- outreach plan/run/verify pipeline in WP admin
- index discover/track/submit/audit/report pipeline in WP admin
- New WordPress settings sections for Monitor / Outreach / Index parameters.
- New monitor/index/outreach history persistence option stores for trend and state tracking.

### Changed

- WordPress plugin version bumped to `1.7.0`.
- Root README now documents dual-track deployment (CLI and WordPress) with separate operation paths.
- `standalone/README.md` rewritten as deployment-first CLI manual.
- `adapters/wordpress/README.md` rewritten as deployment-first plugin manual.
- `adapters/wordpress/readme.txt` stable tag and changelog updated to `1.7.0`.

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

## [0.7.0] - 2026-03-25

### Added

- New adapter script:
- `scripts/backlink_outreach_adapter.py`
- designed for `geo outreach run --provider command`
- integrates with Apify actor `daniil.poletaev/backlink-building-agent`
- filters actor results by target domain and writes artifact JSON per run
- Adapter guide:
- `docs/backlink-outreach-js-adapter.md`

### Changed

- Command template execution now supports shell-safe placeholders:
- `{domain_q}` `{keyword_q}` `{pitch_url_q}` `{site_name_q}` `{email_subject_q}` `{contact_email_q}` `{contact_page_q}`
- Standalone CLI version and default user-agent bumped to `0.7.0`.

## [0.8.0] - 2026-03-25

### Added

- New built-in outreach provider:
- `geo outreach run --provider apify`
- provider internally runs `scripts/backlink_outreach_adapter.py` so users no longer need to pass command templates for default Apify flow
- new provider args:
- `--apify-token`
- `--apify-actor-id`
- `--apify-adapter-path`
- `--apify-output-dir`
- `--apify-allow-fallback-first`

### Changed

- Root/standalone/examples docs now include built-in `apify` provider usage.
- Standalone CLI version and default user-agent bumped to `0.8.0`.

## [0.9.0] - 2026-03-25

### Added

- Auto second-touch support for outreach lifecycle:
- `outreach verify` now generates:
- `outreach-followup-sequences.md`
- `outreach-followup.csv`
- `outreach run --run-followup-due` can execute follow-up sends for `followup_due` prospects.
- Follow-up content fields persisted on campaign prospects:
- `followup_subject`, `followup_body`, `followup_count`

### Changed

- Built-in `apify` provider and custom command payload now support follow-up sends using follow-up templates when applicable.
- Standalone CLI version and default user-agent bumped to `0.9.0`.

## [0.10.0] - 2026-03-25

### Added

- New standalone `geo index` workflow with subcommands:
- `geo index discover`: build expected index URL pool from sitemap + llms + homepage links with grouping (`core` / `blog` / `low_value` / `other`).
- `geo index track`: track URL-level index state (`indexed` / `not_indexed` / `unknown`), persist snapshot history, and emit change lists:
- newly indexed
- dropped indexed
- long unindexed
- `geo index submit`: submit URL batches via `dry-run`, `google-indexing`, `webhook`, or `command` providers with submission logs.
- `geo index audit`: diagnose non-index causes (`noindex`, canonical conflict, soft-404, thin content, crawl failures, weak internal links, missing llms coverage) and output prioritized fixes.
- `geo index report`: generate weekly trend report from historical track snapshots (index rate, deindex rate, recovery rate, template performance).

### Changed

- Root README, standalone README, and examples updated with index workflow commands.
- Standalone CLI version and default user-agent bumped to `0.10.0`.
