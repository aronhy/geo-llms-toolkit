=== GEO LLMS Auto Regenerator ===
Contributors: houyu0729
Tags: seo, llms, schema, sitemap, cloudflare
Requires at least: 6.0
Tested up to: 6.9
Requires PHP: 7.4
Stable tag: 1.9.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Auto-regenerate llms.txt and llms-full.txt, run GEO/SEO health scans, export reports, and optionally purge cache after content updates.

== Description ==

GEO LLMS Auto Regenerator helps WordPress sites keep `llms.txt` and `llms-full.txt` fresh, validate crawl-critical GEO signals, and reduce drift between the frontend and scan results.

Main features:

* Auto-regenerate `llms.txt` and `llms-full.txt` when published content changes.
* Scan key endpoints: `robots.txt`, `sitemap.xml`, `sitemap_index.xml`, `wp-sitemap.xml`, `llms.txt`, `llms-full.txt`.
* Scan key GEO/SEO signals: homepage H1, `link rel="llms"`, canonical, OG/Twitter, `og:image`, article schema, author page signals, breadcrumb schema, soft 404 behavior, `noindex` conflicts, and homepage/article fetch consistency.
* Safe-fix workflow with preview, apply, rollback, and restore defaults.
* Optional issue-driven auto-fix after scans: regenerate missing LLMS files, enforce homepage LLMS link output, enable low-value noindex, and enable WP-layer endpoint fallback for robots/sitemaps.
* Safe-fix mode levels: `Strict` (default, low-risk only, no H1/H2/CSS/UI changes) and `Balanced` (adds fallback OG/Twitter + Schema when needed).
* LLMS rule center with post type filters, taxonomy filters, manual pinning, exclusions, and per-post custom LLMS summary.
* Scheduled scans with history, trend tracking, email/webhook alerts, and Markdown/JSON/CSV report export.
* Optional GEO Agent loop for scheduled runs: scan, safe auto-fix, verify, and rollback on regression.
* Optional cache integration for common WordPress page cache plugins and Cloudflare cache purge.
* Settings import/export, uninstall cleanup, event logs, capability control, and basic i18n loading.

This plugin is designed for site owners, operators, and developers who want a practical GEO operations layer inside WordPress without editing theme code for every basic fix.

== Installation ==

1. Upload the plugin folder to `/wp-content/plugins/`, or install the ZIP from the WordPress admin plugins screen.
2. Activate the plugin through the `Plugins` screen in WordPress.
3. Go to `Settings -> GEO LLMS Auto`.
4. Review your LLMS rules, scan schedule, notifications, and optional cache settings.
5. Run `Regenerate llms files` and `Run GEO scan` once to initialize baseline data.

From this source repository, you can build an install ZIP with:

`./scripts/build-wordpress-zip.sh`

== Frequently Asked Questions ==

= Does this plugin require an external service? =

No. The core plugin works without any external service.

Optional integrations are available for:

* Cloudflare cache purge
* Webhook notifications to a URL chosen by the site administrator

= Does the plugin replace a full SEO plugin? =

No. It complements existing SEO plugins. When common SEO plugins are detected, this plugin avoids forcing fallback OG/schema output.

= Can I control what goes into llms.txt? =

Yes. You can filter by post type, taxonomy, pin important content, exclude URLs/slugs/IDs, and add per-post custom LLMS summaries.

= Can I export reports for clients or developers? =

Yes. The plugin can export the latest scan as Markdown, JSON, or CSV.

== External Services ==

This plugin can optionally connect to external services only when a site administrator enables them in the plugin settings.

= Cloudflare Cache Purge =

Used only if `Cache integration` and `Cloudflare purge` are enabled and the site administrator provides a Cloudflare Zone ID and API token.

Purpose:

* Purge Cloudflare cache after LLMS regeneration so scans and visitors see fresh content.

What is sent:

* Zone ID in the request path
* Either a list of site URLs to purge or `purge_everything=true`, depending on the admin-selected mode

Service endpoints:

* `https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache`

Service terms and privacy:

* Terms: https://www.cloudflare.com/terms/
* Privacy Policy: https://www.cloudflare.com/privacypolicy/

= Webhook Notifications =

Used only if `Webhook notifications` are enabled and the site administrator provides a webhook URL.

Purpose:

* Send GEO scan alerts to an endpoint chosen by the site administrator.

What is sent:

* Site name
* Site URL
* Scan time
* Trigger type
* Overall status
* Summary counts
* Trend summary
* Issues
* Recommendations

Where it is sent:

* The exact webhook URL configured by the site administrator

Service terms and privacy:

* Determined by the webhook provider selected by the site administrator

== Changelog ==

= 1.9.0 =

* Completed CLI-to-WordPress parity for monitor/outreach controls:
* Added monitor brand token settings (equivalent to CLI `--brand-token`).
* Added outreach exclude domains settings (equivalent to CLI `--exclude-domain`).
* Added outreach contact enrichment toggle (equivalent to CLI `--enrich-contacts`).
* Added outreach apify fallback-first toggle (equivalent to CLI `--apify-allow-fallback-first`).
* Wired the new controls into real plan/run pipelines and reportable configuration exports.

= 1.8.0 =

* Added Monitor diff export in WordPress workbench (`markdown/json/csv`).
* Added Outreach status export and manual status update (`domain -> new status`).
* Added Outreach provider `apify` with actor/token settings.
* Added Outreach run controls:
* include existing prospects within cooldown window
* run followup_due queue
* Added Index submit `notification type` (`URL_UPDATED` / `URL_DELETED`).
* Added strict-search and alert settings parity in WordPress settings/workbench.

= 1.7.0 =

* Migrated standalone CLI core workflows into WordPress admin workbench:
* Monitor run pipeline
* Outreach plan/run/verify pipeline
* Index discover/track/submit/audit/report pipeline
* Added full settings panels for Monitor / Outreach / Index in plugin admin.
* Added history persistence for monitor/index/outreach runs.
* Updated plugin version and distribution metadata to 1.7.0.

= 1.6.0 =

* Added GEO Agent loop mode for scheduled runs (`scan -> auto-fix -> verify -> rollback if degraded`).
* Added manual `Run GEO Agent` action in plugin admin.
* Added `agent_mode_enabled` setting to switch scheduled scans to agent loop mode.
* Updated plugin version and distribution metadata to 1.6.0.

= 1.5.0 =

* Added issue-driven auto-fix mode after scans (configurable).
* Added safe-fix mode levels (`Strict` / `Balanced`) to control automatic fix scope.
* Added automatic LLMS file regeneration when scan finds llms endpoints broken and root files are missing/empty.
* Added configurable homepage LLMS link output switch and auto-fix hook.
* Added WP-layer endpoint fallback fixer for `robots.txt`, `sitemap.xml`, `sitemap_index.xml`, `wp-sitemap.xml`.
* Added scheduled/manual scan auto-fix toggles in settings.

= 1.4.0 =

* Added cache integration for Cloudflare and common WordPress page cache plugins.
* Added extended GEO checks for canonical, OG image, author pages, breadcrumb schema, soft 404, noindex conflicts, and fetch consistency.
* Added Markdown/JSON/CSV report export.
* Added settings import/export, uninstall cleanup, event logs, capability control, and version migration scaffolding.
* Added WordPress.org submission metadata and standardized readme support.

= 1.3.0 =

* Added safe-fix preview, rollback, scheduled scans, history, and notifications.

= 1.2.0 =

* Added LLMS rule center, per-post controls, and SEO compatibility detection.

= 1.1.1 =

* Added homepage `link rel="llms"` output and scan support.

= 1.1.0 =

* Added GEO scan page and safe-fix workflow.

= 1.0.0 =

* Initial public release with automatic LLMS regeneration.

== Upgrade Notice ==

= 1.9.0 =

Adds missing CLI parity controls for monitor/outreach and applies them in WordPress runtime pipelines.

= 1.8.0 =

Adds Phase2 CLI parity in WordPress: monitor diff, outreach status/update, apify provider, and index notification type controls.

= 1.7.0 =

Adds full Monitor / Outreach / Index operational workflows directly inside WordPress admin.

= 1.6.0 =

Adds optional GEO Agent closed-loop automation for scheduled runs with automatic rollback guardrails.

= 1.5.0 =

Adds issue-driven auto-fix capability for LLMS/link/noindex/endpoint issues with configurable scan-time execution.

= 1.4.0 =

Adds WordPress.org-ready metadata, report export, cache integration, expanded checks, and plugin infrastructure features.
