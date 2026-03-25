# geo-llms-toolkit

Open-source GEO toolkit. Download and run directly on any website (not only WordPress).

## 1) Download and run (Standalone CLI)

```bash
git clone https://github.com/aronhy/geo-llms-toolkit.git
cd geo-llms-toolkit
chmod +x geo
./geo --help
```

### Quick commands

```bash
# GEO scan
./geo scan aronhouyu.com

# Export scan report
./geo scan aronhouyu.com --format json --output ./output/scan.json

# Generate llms.txt and llms-full.txt
./geo llms aronhouyu.com --output-dir ./output

# One-shot (scan + llms generation)
./geo all aronhouyu.com --output-dir ./output --report-format markdown

# Competitor monitor (keywords + competitor domains)
./geo monitor aronhouyu.com \
  --keywords-file ./examples/keywords.txt \
  --competitor example.com \
  --discover-competitors \
  --format json \
  --output ./output/monitor.json

# Compare monitor snapshots
./geo monitor-diff \
  --current-report ./output/monitor.json \
  --previous-report ./.geo-history/monitor-aronhouyu.com-YYYYMMDDTHHMMSSZ.json \
  --output ./output/monitor-diff.md

# Build outreach plan from monitor result
./geo outreach \
  plan \
  --monitor-report ./output/monitor.json \
  --pitch-url https://aronhouyu.com/your-best-page \
  --site-name "Aron Houyu" \
  --enrich-contacts \
  --output-dir ./output/outreach

# Execute outreach (default only-new with cooldown)
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider dry-run

# Verify link wins and auto-mark followup_due
./geo outreach verify \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --followup-days 7

# Execute outreach via webhook
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider webhook \
  --webhook-url https://your-automation-endpoint.example/webhook \
  --webhook-token YOUR_TOKEN

# Execute outreach via backlink-outreach-js adapter
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider apify \
  --apify-token "$APIFY_TOKEN" \
  --apify-output-dir ./output/outreach/apify

# Show campaign status
./geo outreach status \
  --campaign-file ./output/outreach/outreach-campaign.json
```

Standalone guide: [standalone/README.md](./standalone/README.md)
backlink adapter guide: [docs/backlink-outreach-js-adapter.md](./docs/backlink-outreach-js-adapter.md)

## 2) If your site is WordPress

WordPress adapter is still included in this repository:

- `adapters/wordpress` (production adapter)
- Includes llms auto-regeneration, GEO scan, safe-fix flow, report export, optional cache purge.

Install docs: [adapters/wordpress/readme.txt](./adapters/wordpress/readme.txt)
Detailed setup (BT + Nginx + Cloudflare): [docs/wordpress-detailed-setup.md](./docs/wordpress-detailed-setup.md)

## 3) Latest updates (Standalone 0.8.0 + WordPress 1.6.0)

- **New CLI competitor monitor** (`geo monitor`): keyword-based competitor scoring, brand/non-brand keyword split, prioritized action list (`P0/P1/P2`), and history snapshots for weekly trend tracking.
- **Monitor improvements**: configurable weights (`--weights-file`) and snapshot compare (`geo monitor-diff`).
- **Outreach workflow** (`geo outreach plan/run/status/verify/update`): plan generation + campaign state + executable run layer (dry-run/webhook/command), contact enrichment, only-new strategy, cooldown dedupe, and win/followup verification.
- **Apify built-in provider** (`--provider apify`): direct integration via `scripts/backlink_outreach_adapter.py` without hand-writing command templates.

- **Issue-driven auto safe-fix**: regenerate missing `llms.txt` / `llms-full.txt`, enforce homepage `<link rel="llms" href="/llms.txt">`, enable low-value page `noindex`, and enable WP-layer endpoint fallback for `robots.txt` / `sitemap.xml` / `sitemap_index.xml` / `wp-sitemap.xml`.
- **Safe-fix mode levels**: `Strict` (default, low-risk only, no H1/H2/CSS/UI structural edits) and `Balanced` (extends with fallback OG/Twitter + Schema output without changing template structure).
- **GEO Agent loop mode**: supports `scan -> auto-fix -> verify -> rollback if degraded`, with manual action button and scheduled mode switch.
- **WordPress release ZIP packaging**: build with `./scripts/build-wordpress-zip.sh`; latest package in repo is `dist/geo-llms-auto-regenerator-1.6.0.zip`.

## 4) Repository layout

```text
geo-llms-toolkit/
  geo                      # standalone CLI launcher
  standalone/              # standalone implementation
  adapters/
    wordpress/             # WordPress adapter
  core/
    docs/                  # engine contracts/design
  docs/
    architecture.md
    roadmap.md
    migration-plan.md
  examples/
```

## 5) Common dev commands

```bash
# WordPress adapter lint
php -l adapters/wordpress/geo-llms-auto-regenerator.php
php -l adapters/wordpress/uninstall.php
php -l adapters/wordpress/languages/index.php

# build WordPress install ZIP
./scripts/build-wordpress-zip.sh

# standalone CLI help
python3 standalone/geo_toolkit.py --help
```

## Open-source policy

- License: `GPL-2.0-or-later`
- Issues and pull requests are welcome
- Keep reusable logic in `core`/`standalone`, keep platform hooks in `adapters/*`

## Follow

- X: https://x.com/aronhouyu
- YouTube: https://www.youtube.com/@aronhouyu1024
