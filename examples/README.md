# Examples

This folder stores usage examples for standalone and adapter modes.

## Standalone CLI examples

```bash
./geo scan example.com --format markdown --output ./output/scan.md
./geo llms example.com --output-dir ./output
./geo all example.com --output-dir ./output --report-format json
./geo monitor example.com --keywords-file ./examples/keywords.txt --discover-competitors --format json --output ./output/monitor.json
./geo monitor-diff --current-report ./output/monitor.json --previous-report ./baseline-monitor.json --output ./output/monitor-diff.md
./geo outreach plan --monitor-report ./output/monitor.json --pitch-url https://example.com/guide --output-dir ./output/outreach
./geo outreach run --campaign-file ./output/outreach/outreach-campaign.json --provider dry-run
./geo outreach run --campaign-file ./output/outreach/outreach-campaign.json --provider command --command-template 'python3 ./scripts/backlink_outreach_adapter.py --domain {domain_q} --keyword {keyword_q} --pitch-url {pitch_url_q}'
./geo outreach verify --campaign-file ./output/outreach/outreach-campaign.json --followup-days 7
./geo outreach update --campaign-file ./output/outreach/outreach-campaign.json --domain example.org --new-status won --note "link placed"
./geo outreach status --campaign-file ./output/outreach/outreach-campaign.json
```

## Adapter examples (planned)

- WordPress deployment and verification checklist
- Shopify webhook mapping example
