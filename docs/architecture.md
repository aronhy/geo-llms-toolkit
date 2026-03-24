# Architecture

## Goal

Split GEO capabilities into:

- `core` (platform-neutral engine)
- `adapters/*` (platform bindings)

## Core responsibilities

- endpoint probing (`robots`, `sitemaps`, `llms*`)
- signal checks (`canonical`, `og`, schema, noindex, soft-404, fetch consistency)
- recommendation generation
- report serialization (`json`, `csv`, `markdown`)
- notification payload normalization

## Adapter responsibilities

- content discovery from platform data model
- file writing and publish hooks
- UI / settings storage
- cache integration provider wiring

## Current state

- `adapters/wordpress`: full implementation exists
- `core`: to be extracted incrementally from WordPress adapter internals

## Extraction strategy

1. Move report builders into `core`.
2. Move scan models and result schema into `core`.
3. Move check runners into `core` with adapter-provided fetch/content APIs.
4. Keep WordPress-specific admin UI and hooks in adapter.
