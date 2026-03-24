# Adapter Contract (Draft)

This contract is the planned interface between `core` and each platform adapter.

```text
interface Adapter {
  getSiteIdentity() -> { name, url, locale }
  listCorePages(limit) -> Page[]
  listRecentContent(limit) -> ContentItem[]
  listPinnedContent(limit) -> ContentItem[]
  listLowValueTargets() -> Target[]
  fetch(url, options) -> HttpResponse
  writeFile(path, content) -> Result
  notify(event, payload) -> Result
  purgeCache(context) -> Result
}
```

`core` consumes this contract to:

- generate `llms.txt` / `llms-full.txt`
- execute checks
- compute summary/trend/recommendations
- export reports
