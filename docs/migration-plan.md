# Migration Plan (WordPress -> Multi-platform Toolkit)

## Phase 1: Stabilize public baseline

- keep WordPress adapter unchanged for users
- expose clear adapter boundary docs
- publish open-source governance files

## Phase 2: Core extraction

- define `ScanCheck`, `ScanSummary`, `Recommendation` model
- extract report serialization from WordPress adapter
- extract endpoint/signal evaluation helpers into `core`

## Phase 3: Adapter contract (Completed baseline)

Define minimal adapter interface:

- `fetch(url, options)`
- `listHighValuePages()`
- `listLowValuePages()`
- `writeIndexFiles(llms, llmsFull)`
- `sendNotification(payload)`
- `purgeCache(context)`

Delivered:

- Python contract types at `core/python/adapter_contract.py`
- Standalone implementation `StandaloneWebAdapter`
- CLI self-check command `geo adapter-check`

## Phase 4: Shopify adapter

- support blog/article/page/product high-value discovery
- build webhook-driven regeneration flow
- implement adapter-specific exclusions and cache purge strategy

## Phase 5: Standalone CLI

- config-based static site mode (`yaml/json`)
- generate `llms*.txt` from sitemap + include/exclude rules
- run scan and export report in CI
