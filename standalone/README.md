# Standalone CLI

`geo-llms-toolkit` now supports non-WordPress websites through a standalone CLI.

## Requirements

- Python 3.9+

## Quick start

```bash
git clone https://github.com/aronhy/geo-llms-toolkit.git
cd geo-llms-toolkit
chmod +x geo
./geo --help
```

## CLI feature overview

Current standalone CLI supports 5 capability groups:

| Capability | Command | Purpose | Main outputs |
| --- | --- | --- | --- |
| GEO site checks | `geo scan` | Check endpoint/indexability/schema signals | terminal output or `scan.(md/json/csv)` |
| LLMS generation | `geo llms` | Generate `llms.txt` and `llms-full.txt` from sitemap/content | `llms.txt`, `llms-full.txt` |
| One-shot pipeline | `geo all` | Run `scan + llms` together | scan report + llms files |
| Competitor monitoring | `geo monitor` | Keyword-based visibility monitoring, configurable scoring, priority action suggestions | `monitor.(md/json/csv)`, monitor snapshots |
| Competitor trend diff | `geo monitor-diff` | Compare two monitor reports (score delta/action delta) | `monitor-diff.(md/json/csv)` |
| Outreach execution workflow | `geo outreach plan/run/status/verify/update` | Build prospect plan, execute runs, verify wins, update status, and track campaign state with only-new cooldown strategy | outreach reports, campaign JSON, status reports |

Outreach execution providers:
- `dry-run`: simulate execution and update campaign state only.
- `webhook`: send one payload per prospect to your automation endpoint (n8n/Make/custom API).
- `command`: execute your command template per prospect (adapter mode, can wrap `backlink-outreach-js`).

## Commands

```bash
# 1) GEO scan (default markdown output to stdout)
./geo scan aronhouyu.com

# 2) GEO scan and save JSON report
./geo scan aronhouyu.com --format json --output ./output/scan.json

# 3) Build llms.txt + llms-full.txt
./geo llms aronhouyu.com --output-dir ./output

# 4) One-shot run (scan + llms files)
./geo all aronhouyu.com --output-dir ./output --report-format markdown

# 5) Competitor monitor (keyword-based)
./geo monitor aronhouyu.com \
  --keywords-file ./examples/keywords.txt \
  --competitor example.com \
  --competitor another.com \
  --discover-competitors \
  --format json \
  --output ./output/monitor.json

# 6) Monitor diff (current vs previous snapshot)
./geo monitor-diff \
  --current-report ./output/monitor.json \
  --previous-report ./.geo-history/monitor-aronhouyu.com-YYYYMMDDTHHMMSSZ.json \
  --output ./output/monitor-diff.md

# 7) Build outreach plan from monitor result
./geo outreach \
  plan \
  --monitor-report ./output/monitor.json \
  --pitch-url https://aronhouyu.com/your-best-page \
  --site-name "Aron Houyu" \
  --enrich-contacts \
  --output-dir ./output/outreach

# 8) Execute outreach campaign (only-new by default)
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider dry-run

# 9) Show campaign status
./geo outreach status \
  --campaign-file ./output/outreach/outreach-campaign.json

# 10) Verify backlinks + auto mark followup_due
./geo outreach verify \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --followup-days 7

# 11) Manual status update (replied/won/lost)
./geo outreach update \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --domain example.com \
  --new-status won \
  --note "link added on resources page"

# 12) Execute via webhook automation (n8n/Make/custom API)
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider webhook \
  --webhook-url https://your-automation-endpoint.example/webhook \
  --webhook-token YOUR_TOKEN

# 13) Execute via built-in Apify provider (backlink-outreach-js adapter)
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider apify \
  --apify-token "$APIFY_TOKEN" \
  --apify-output-dir ./output/outreach/apify

# 14) Execute via custom command adapter
./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider command \
  --command-template 'python3 ./scripts/backlink_outreach_adapter.py --domain {domain_q} --keyword {keyword_q} --pitch-url {pitch_url_q} --site-name {site_name_q} --contact-email {contact_email_q} --contact-page {contact_page_q} --output-dir ./output/outreach/apify'
```

## Output files

- `llms.txt`
- `llms-full.txt`
- `geo-scan-report.(md|json|csv)` when using `all` or `scan --output`
- `monitor.(md|json|csv)` when using `monitor --output`
- `monitor-diff.(md|json|csv)` when using `monitor-diff`
- `.geo-history/monitor-<domain>-<timestamp>.json` snapshot (default)
- `outreach-plan.json`, `outreach-prospects.csv`, `outreach-report.md`, `outreach-sequences.md` when using `outreach`
- `outreach-campaign.json` campaign state file for `outreach run/status`
- `.geo-history/outreach-state.json` cross-run dedupe state (default)
- `output/outreach/apify/apify-outreach-*.json` adapter artifacts when using backlink adapter

## Monitor keywords file format

TXT (one keyword per line):

```txt
geo llms
wordpress geo
ai seo tools
```

CSV/TSV (recommended):

```csv
keyword,group,value
geo llms,core,1.5
wordpress geo,core,1.2
ai seo tools,expansion,1.0
```

- `group` and `value` are optional.
- `value` is used as lightweight business importance weight.

Optional monitor weights JSON:

```json
{
  "keyword_overlap": 0.45,
  "serp_coappear": 0.35,
  "rank_pressure": 0.20
}
```

## Notes

- Domain input can be `aronhouyu.com` or full URL `https://aronhouyu.com`.
- Sitemap discovery checks `/sitemap.xml`, `/sitemap_index.xml`, `/wp-sitemap.xml`.
- If sitemap discovery fails, llms generation falls back to homepage-only output.
- `monitor` currently uses Bing SERP HTML as default provider.
- `outreach run` providers:
- `dry-run`: update campaign status without sending.
- `webhook`: POST each prospect payload to your automation endpoint.
- `apify`: built-in backlink-outreach-js adapter execution.
- `command`: execute your command template per prospect (for custom adapters).
- command template variables:
- raw: `{domain}` `{keyword}` `{pitch_url}` `{site_name}` `{email_subject}` `{contact_email}` `{contact_page}`
- shell-safe: `{domain_q}` `{keyword_q}` `{pitch_url_q}` `{site_name_q}` `{email_subject_q}` `{contact_email_q}` `{contact_page_q}`
- for `scripts/backlink_outreach_adapter.py`, set `APIFY_TOKEN` in environment first.
- adapter guide: `../docs/backlink-outreach-js-adapter.md`
