=== GEO LLMS Auto Regenerator ===
Contributors: houyu0729
Tags: seo, llms, schema, sitemap, cloudflare
Requires at least: 6.0
Tested up to: 6.9
Requires PHP: 7.4
Stable tag: 1.4.0
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
* LLMS rule center with post type filters, taxonomy filters, manual pinning, exclusions, and per-post custom LLMS summary.
* Scheduled scans with history, trend tracking, email/webhook alerts, and Markdown/JSON/CSV report export.
* Optional cache integration for common WordPress page cache plugins and Cloudflare cache purge.
* Settings import/export, uninstall cleanup, event logs, capability control, and basic i18n loading.

This plugin is designed for site owners, operators, and developers who want a practical GEO operations layer inside WordPress without editing theme code for every basic fix.

== Installation ==

1. Upload the plugin folder to `/wp-content/plugins/`, or install the ZIP from the WordPress admin plugins screen.
2. Activate the plugin through the `Plugins` screen in WordPress.
3. Go to `Settings -> GEO LLMS Auto`.
4. Review your LLMS rules, scan schedule, notifications, and optional cache settings.
5. Run `Regenerate llms files` and `Run GEO scan` once to initialize baseline data.

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

= 1.4.0 =

Adds WordPress.org-ready metadata, report export, cache integration, expanded checks, and plugin infrastructure features.
