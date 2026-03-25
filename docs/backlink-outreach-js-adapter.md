# backlink-outreach-js Adapter Guide

This project includes a command adapter script:

- `scripts/backlink_outreach_adapter.py`

Use it with:

- `geo outreach run --provider command`

The adapter calls Apify actor `daniil.poletaev/backlink-building-agent` (from `backlink-outreach-js`) and writes per-domain execution artifacts.

## 1. Prerequisites

1. Get an Apify API token.
2. Export token:

```bash
export APIFY_TOKEN=apify_api_xxx
```

## 2. Build campaign first

```bash
./geo monitor yourdomain.com --keywords-file ./examples/keywords.txt --format json --output ./output/monitor.json
./geo outreach plan --monitor-report ./output/monitor.json --pitch-url https://yourdomain.com/best-page --enrich-contacts --output-dir ./output/outreach
```

## 3. Run with command adapter

```bash
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider command \
  --command-template 'python3 ./scripts/backlink_outreach_adapter.py --domain {domain_q} --keyword {keyword_q} --pitch-url {pitch_url_q} --site-name {site_name_q} --contact-email {contact_email_q} --contact-page {contact_page_q} --output-dir ./output/outreach/apify'
```

## 4. Adapter behavior

- Input source: one prospect payload from `geo outreach run`.
- Actor request: one keyword (`top_gap_keyword`) per run.
- Domain filter: keeps actor items where `articleUrl` matches the target domain.
- Exit code:
- `0`: success (at least one matched item)
- `4`: no domain-matched result (marked failed in campaign)
- Artifacts:
- `output/outreach/apify/apify-outreach-<domain>-<timestamp>.json`

## 5. Optional flags for adapter

```bash
python3 ./scripts/backlink_outreach_adapter.py --help
```

Useful flags:

- `--allow-fallback-first`: if no domain match, use first actor item as fallback.
- `--business-name`: override actor `businessName`.
- `--business-description`: override actor `shortBusinessDescription`.
- `--contact-name`: override actor `name`.
- `--dry-run`: print payload only, no actor request.

## 6. Notes

- This adapter is execution glue, not a full CRM/outreach sender.
- For actual sending/followups, pair with:
- `geo outreach verify`
- `geo outreach update`
- your webhook/CRM automation.
