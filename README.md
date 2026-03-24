# geo-llms-toolkit

Open-source GEO toolkit for website discovery, crawlability, and AI-readable index generation.

Current status:

- `adapters/wordpress` is production-ready and includes:
- auto-regeneration of `llms.txt` and `llms-full.txt`
- GEO endpoint/signal scan
- safe-fix workflow
- report export (Markdown / JSON / CSV)
- optional cache purge integration

Next target:

- extract reusable engine into `core`
- add non-WordPress adapters (`shopify`, `standalone-cli`)

## Why this repo

The original project was WordPress-focused. This repository is the open-source foundation to make GEO checks and LLMS indexing usable across platforms.

## Repository layout

```text
geo-llms-toolkit/
  adapters/
    wordpress/        # current working adapter (PHP plugin)
  core/
    docs/             # engine contracts and design docs
  docs/
    architecture.md
    roadmap.md
    migration-plan.md
  examples/
```

## Quick start (WordPress adapter)

1. Go to `adapters/wordpress`.
2. Install plugin ZIP from that adapter or upload plugin files into WordPress.
3. Enable plugin and configure settings in `Settings -> GEO LLMS Auto`.

See adapter docs:

- [WordPress adapter readme](./adapters/wordpress/readme.txt)

## Open-source policy

- License: `GPL-2.0-or-later`
- Issues and pull requests are welcome
- Keep platform-neutral logic in `core`, not in adapter-specific code

## Roadmap

See [docs/roadmap.md](./docs/roadmap.md).

## Follow

- X: https://x.com/aronhouyu
- YouTube: https://www.youtube.com/@aronhouyu1024
