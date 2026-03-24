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
```

Standalone guide: [standalone/README.md](./standalone/README.md)

## 2) If your site is WordPress

WordPress adapter is still included in this repository:

- `adapters/wordpress` (production adapter)
- Includes llms auto-regeneration, GEO scan, safe-fix flow, report export, optional cache purge.

Install docs: [adapters/wordpress/readme.txt](./adapters/wordpress/readme.txt)

## 3) Repository layout

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

## 4) Common dev commands

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
