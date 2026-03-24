# Contributing

## Principles

- Keep changes small and reviewable.
- Do not break existing WordPress adapter behavior.
- New platform logic should go through adapter boundary, not hardcoded in core.

## Development flow

1. Create a branch from `main`.
2. Add or update docs when behavior changes.
3. Run local checks relevant to changed adapter.
4. Open PR with:
- what changed
- why
- backward compatibility impact

## Coding scope

- `core/`: platform-neutral logic only
- `adapters/wordpress/`: WordPress hooks, settings, rendering, and WP-specific integrations

## Issue labels (suggested)

- `type:bug`
- `type:feature`
- `area:core`
- `area:wordpress`
- `area:shopify`
- `area:docs`
