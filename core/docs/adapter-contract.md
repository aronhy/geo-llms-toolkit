# Adapter Contract (Phase 3)

This contract is now implemented in:

- `core/python/adapter_contract.py`

Current contract methods:

```text
get_site_identity() -> AdapterSiteIdentity
get_capabilities() -> AdapterCapabilities
fetch(url, options) -> AdapterHttpResponse
list_high_value_pages(limit) -> AdapterPage[]
list_low_value_pages(limit) -> AdapterPage[]
write_index_files(llms_text, llms_full_text) -> AdapterActionResult
send_notification(payload) -> AdapterActionResult
purge_cache(context) -> AdapterActionResult
```

Type models included:

- `AdapterSiteIdentity`
- `AdapterPage`
- `AdapterFetchOptions`
- `AdapterHttpResponse`
- `AdapterActionResult`
- `AdapterCapabilities` (`can_write_index_files`, `can_auto_fix`, `can_purge_cache`)

Why this exists:

- make `core` reusable across WordPress / Shopify / static-site adapters
- avoid tightly coupling scan/llms logic to one runtime
- keep adapter-specific side effects (write file / notify / cache purge) isolated

Current implementation status:

- Standalone CLI ships `StandaloneWebAdapter` implementing this contract.
- Standalone CLI also provides `ShopifyReadOnlyAdapter` and `GenericHttpReadOnlyAdapter` for read-only scans/diagnostics.
- WordPress adapter remains integrated in plugin runtime and will be incrementally wired to this contract in next phases.
