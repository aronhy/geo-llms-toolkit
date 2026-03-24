# Roadmap

## v0.1.0 (current)

- Publish repository
- Include working WordPress adapter
- Document migration plan to multi-platform

## v0.2.0

- Add `standalone-cli` for non-WordPress sites (delivered)
- Support `scan / llms / all` commands (delivered)
- Standalone docs and quickstart launcher (delivered)
- Begin extraction of reusable logic from adapter-specific code

## v0.3.0

- Extract first reusable `core` package:
- scan result schema
- report exporters (`json/csv/markdown`)
- recommendation engine

- Add `shopify` adapter (app extension or webhook worker mode)
- Support automatic `llms*.txt` generation from product/blog/page updates

## v0.4.0

- Add config-driven scan profile (`yaml/json`)
- Add optional plugin/adapter bridge layer for more CMS

## v0.5.0

- Add GitHub Action template for scheduled GEO scans
- Add webhook/email notifier module in `core`

## Non-goals for now

- full crawler replacement
- rank tracking platform
- keyword intelligence suite
