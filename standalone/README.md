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
  --format markdown \
  --output ./output/monitor.md
```

## Output files

- `llms.txt`
- `llms-full.txt`
- `geo-scan-report.(md|json|csv)` when using `all` or `scan --output`
- `monitor.(md|json|csv)` when using `monitor --output`
- `.geo-history/monitor-<domain>-<timestamp>.json` snapshot (default)

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

## Notes

- Domain input can be `aronhouyu.com` or full URL `https://aronhouyu.com`.
- Sitemap discovery checks `/sitemap.xml`, `/sitemap_index.xml`, `/wp-sitemap.xml`.
- If sitemap discovery fails, llms generation falls back to homepage-only output.
- `monitor` currently uses Bing SERP HTML as default provider.
