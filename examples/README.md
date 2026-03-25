# Examples

This folder stores usage examples for standalone and adapter modes.

## Standalone CLI examples

```bash
./geo scan example.com --format markdown --output ./output/scan.md
./geo llms example.com --output-dir ./output
./geo all example.com --output-dir ./output --report-format json
./geo monitor example.com --keywords-file ./examples/keywords.txt --discover-competitors --format json --output ./output/monitor.json
./geo outreach plan --monitor-report ./output/monitor.json --pitch-url https://example.com/guide --output-dir ./output/outreach
./geo outreach run --campaign-file ./output/outreach/outreach-campaign.json --provider dry-run
./geo outreach status --campaign-file ./output/outreach/outreach-campaign.json
```

## Adapter examples (planned)

- WordPress deployment and verification checklist
- Shopify webhook mapping example
