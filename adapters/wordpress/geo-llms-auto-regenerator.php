<?php
/**
 * Plugin Name: GEO LLMS Auto Regenerator
 * Description: Auto-regenerate llms.txt and llms-full.txt, scan GEO health, and apply safe fixes.
 * Version: 1.5.0
 * Requires at least: 6.0
 * Requires PHP: 7.4
 * Author: aronhouyu
 * Author URI: https://aronhouyu.com/
 * License: GPL v2 or later
 * License URI: https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain: geo-llms-auto-regenerator
 * Domain Path: /languages
 */

/*
GEO LLMS Auto Regenerator is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 2 of the License, or
any later version.

GEO LLMS Auto Regenerator is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with GEO LLMS Auto Regenerator. If not, see https://www.gnu.org/licenses/gpl-2.0.html.
*/

if (!defined('ABSPATH')) {
    exit;
}

final class GEO_LLMS_Auto_Regenerator {
    const VERSION = '1.5.0';
    const ADMIN_SLUG = 'geo-llms-auto';
    const EVENT_HOOK = 'geo_llms_autogen_regenerate';
    const OPTION_KEY = 'geo_llms_autogen_last_result';
    const SCAN_OPTION_KEY = 'geo_llms_autogen_last_scan';
    const SCAN_HISTORY_OPTION_KEY = 'geo_llms_autogen_scan_history';
    const SETTINGS_OPTION_KEY = 'geo_llms_autogen_settings';
    const NOTICE_OPTION_KEY = 'geo_llms_autogen_notice';
    const PREVIEW_OPTION_KEY = 'geo_llms_autogen_fix_preview';
    const SETTINGS_BACKUP_OPTION_KEY = 'geo_llms_autogen_settings_backup';
    const VERSION_OPTION_KEY = 'geo_llms_autogen_plugin_version';
    const LOG_OPTION_KEY = 'geo_llms_autogen_logs';
    const CACHE_QUEUE_OPTION_KEY = 'geo_llms_autogen_cache_queue';
    const META_SUMMARY_KEY = '_geo_llms_summary';
    const META_PIN_KEY = '_geo_llms_pin';
    const META_EXCLUDE_KEY = '_geo_llms_exclude';
    const SCAN_CRON_HOOK = 'geo_llms_autogen_scheduled_scan';

    public static function init() {
        add_action('init', array(__CLASS__, 'load_textdomain'));
        add_action('init', array(__CLASS__, 'maybe_upgrade'));
        add_action(self::EVENT_HOOK, array(__CLASS__, 'regenerate_files'));

        add_action('save_post', array(__CLASS__, 'on_save_post'), 10, 3);
        add_action('transition_post_status', array(__CLASS__, 'on_transition_post_status'), 10, 3);
        add_action('deleted_post', array(__CLASS__, 'on_deleted_post'));
        add_action('untrashed_post', array(__CLASS__, 'on_deleted_post'));

        add_action('admin_menu', array(__CLASS__, 'register_admin_page'));
        add_action('admin_post_geo_llms_regenerate_now', array(__CLASS__, 'handle_manual_regenerate'));
        add_action('admin_post_geo_llms_run_scan', array(__CLASS__, 'handle_run_scan'));
        add_action('admin_post_geo_llms_preview_safe_fixes', array(__CLASS__, 'handle_preview_safe_fixes'));
        add_action('admin_post_geo_llms_apply_safe_fixes', array(__CLASS__, 'handle_apply_safe_fixes'));
        add_action('admin_post_geo_llms_rollback_safe_fixes', array(__CLASS__, 'handle_rollback_safe_fixes'));
        add_action('admin_post_geo_llms_reset_defaults', array(__CLASS__, 'handle_reset_defaults'));
        add_action('admin_post_geo_llms_save_settings', array(__CLASS__, 'handle_save_settings'));
        add_action('admin_post_geo_llms_export_report', array(__CLASS__, 'handle_export_report'));
        add_action('admin_post_geo_llms_export_settings', array(__CLASS__, 'handle_export_settings'));
        add_action('admin_post_geo_llms_import_settings', array(__CLASS__, 'handle_import_settings'));
        add_action('admin_post_geo_llms_clear_logs', array(__CLASS__, 'handle_clear_logs'));
        add_action('add_meta_boxes', array(__CLASS__, 'register_meta_boxes'));
        add_action('save_post', array(__CLASS__, 'save_post_llms_meta'), 20, 2);
        add_action(self::SCAN_CRON_HOOK, array(__CLASS__, 'run_scheduled_scan'));
        add_filter('cron_schedules', array(__CLASS__, 'register_cron_schedules'));

        add_filter('wp_robots', array(__CLASS__, 'filter_wp_robots'));
        add_action('send_headers', array(__CLASS__, 'send_x_robots_header'));
        add_action('login_init', array(__CLASS__, 'send_login_noindex_header'));
        add_action('login_head', array(__CLASS__, 'render_login_noindex_meta'));
        add_filter('redirect_canonical', array(__CLASS__, 'filter_endpoint_canonical_redirect'), 10, 2);
        add_action('template_redirect', array(__CLASS__, 'maybe_serve_endpoint_fallbacks'), 0);
        add_action('wp_head', array(__CLASS__, 'output_llms_link_tag'), 1);
        add_action('wp_head', array(__CLASS__, 'output_fallback_social_meta'), 1);
        add_action('wp_head', array(__CLASS__, 'output_fallback_schema_markup'), 99);
    }

    public static function on_activate() {
        if (!get_option(self::SETTINGS_OPTION_KEY)) {
            update_option(self::SETTINGS_OPTION_KEY, self::get_default_settings(), false);
        }
        update_option(self::VERSION_OPTION_KEY, self::VERSION, false);
        self::sync_scan_schedule();
        self::schedule_regeneration(3);
    }

    public static function on_deactivate() {
        $ts = wp_next_scheduled(self::EVENT_HOOK);
        if ($ts) {
            wp_unschedule_event($ts, self::EVENT_HOOK);
        }
        wp_clear_scheduled_hook(self::SCAN_CRON_HOOK);
    }

    public static function load_textdomain() {
        load_plugin_textdomain('geo-llms-auto-regenerator', false, dirname(plugin_basename(__FILE__)) . '/languages');
    }

    public static function maybe_upgrade() {
        $stored_version = (string) get_option(self::VERSION_OPTION_KEY, '');
        if ($stored_version === self::VERSION) {
            return;
        }

        $settings = self::get_settings();
        $settings = wp_parse_args($settings, self::get_default_settings());
        self::save_settings($settings);
        self::sync_scan_schedule($settings);
        update_option(self::VERSION_OPTION_KEY, self::VERSION, false);
        self::log_event('info', 'plugin_upgraded', array(
            'from' => $stored_version ?: 'fresh-install',
            'to' => self::VERSION,
        ), true);
    }

    public static function on_save_post($post_id, $post, $update) {
        if (wp_is_post_autosave($post_id) || wp_is_post_revision($post_id)) {
            return;
        }

        if (!$post instanceof WP_Post) {
            return;
        }

        if (!self::is_supported_post_type($post->post_type)) {
            return;
        }

        if ($post->post_status !== 'publish') {
            return;
        }

        self::queue_cache_urls(self::get_post_related_cache_urls($post));
        self::schedule_regeneration();
    }

    public static function on_transition_post_status($new_status, $old_status, $post) {
        if (!$post instanceof WP_Post) {
            return;
        }

        if (!self::is_supported_post_type($post->post_type)) {
            return;
        }

        if ($new_status === 'publish' || $old_status === 'publish') {
            self::queue_cache_urls(self::get_post_related_cache_urls($post));
            self::schedule_regeneration();
        }
    }

    public static function on_deleted_post($post_id) {
        $post = get_post($post_id);
        if (!$post instanceof WP_Post) {
            return;
        }

        if (!self::is_supported_post_type($post->post_type)) {
            return;
        }

        self::queue_cache_urls(self::get_post_related_cache_urls($post));
        self::schedule_regeneration();
    }

    private static function is_supported_post_type($post_type) {
        $type_obj = get_post_type_object($post_type);
        return $type_obj && !empty($type_obj->publicly_queryable);
    }

    private static function schedule_regeneration($delay_seconds = 20) {
        if (!wp_next_scheduled(self::EVENT_HOOK)) {
            wp_schedule_single_event(time() + max(1, (int) $delay_seconds), self::EVENT_HOOK);
        }
    }

    public static function regenerate_files() {
        $site_name = trim((string) get_bloginfo('name'));
        $site_desc = trim((string) get_bloginfo('description'));
        $site_url = home_url('/');
        $locale = get_locale();

        if ($site_desc === '') {
            $site_desc = '站点内容索引与核心页面导航。';
        }

        $featured_items = self::get_pinned_items(8);
        $core_pages = self::get_core_pages(8);
        $recent_posts_short = self::get_recent_posts(12);
        $recent_posts_full = self::get_recent_posts(30);
        $topics = self::get_top_terms(12);

        $seen_short = array();
        $featured_items_short = self::dedupe_items_by_url($featured_items, $seen_short);
        $core_pages_short = self::dedupe_items_by_url($core_pages, $seen_short);
        $recent_posts_short = self::dedupe_items_by_url($recent_posts_short, $seen_short);

        $seen_full = array();
        $featured_items_full = self::dedupe_items_by_url($featured_items, $seen_full);
        $core_pages_full = self::dedupe_items_by_url($core_pages, $seen_full);
        $recent_posts_full = self::dedupe_items_by_url($recent_posts_full, $seen_full);

        $llms = self::build_llms_txt($site_name, $site_desc, $site_url, $locale, $featured_items_short, $core_pages_short, $recent_posts_short, $topics);
        $llms_full = self::build_llms_full_txt($site_name, $site_desc, $site_url, $locale, $featured_items_full, $core_pages_full, $recent_posts_full, $topics);

        $ok_a = self::write_root_file('llms.txt', $llms);
        $ok_b = self::write_root_file('llms-full.txt', $llms_full);
        $cache_purge = self::purge_enabled_caches(($ok_a && $ok_b) ? 'regenerate' : 'skip');

        update_option(
            self::OPTION_KEY,
            array(
                'time' => current_time('mysql'),
                'ok' => ($ok_a && $ok_b),
                'llms_bytes' => strlen($llms),
                'llms_full_bytes' => strlen($llms_full),
                'site' => $site_url,
                'cache_purge' => $cache_purge,
            ),
            false
        );
    }

    private static function get_core_pages($limit) {
        $items = array();
        $home = home_url('/');
        $items[] = array(
            'title' => '首页',
            'url' => $home,
            'desc' => '站点入口与内容导航。',
        );

        $pages = get_pages(
            array(
                'sort_column' => 'menu_order,post_title',
                'sort_order' => 'ASC',
                'number' => max(1, (int) $limit),
                'post_status' => 'publish',
            )
        );

        foreach ($pages as $page) {
            if (!($page instanceof WP_Post)) {
                continue;
            }

            if (self::should_exclude_post_from_llms($page)) {
                continue;
            }

            $url = get_permalink($page);
            if (!$url || $url === $home) {
                continue;
            }

            $items[] = array(
                'title' => get_the_title($page),
                'url' => $url,
                'desc' => self::get_llms_description_for_post($page, 64),
            );

            if (count($items) >= $limit + 1) {
                break;
            }
        }

        return $items;
    }

    private static function get_recent_posts($limit) {
        $items = array();
        $settings = self::get_settings();
        $post_types = self::get_included_post_types($settings);
        $args = array(
            'post_type' => $post_types,
            'post_status' => 'publish',
            'posts_per_page' => max(1, (int) $limit * 3),
            'orderby' => 'date',
            'order' => 'DESC',
            'ignore_sticky_posts' => true,
            'no_found_rows' => true,
        );
        $tax_query = self::build_tax_query_for_settings($settings);
        if (!empty($tax_query)) {
            $args['tax_query'] = $tax_query;
        }

        $q = new WP_Query($args);

        if (!$q->have_posts()) {
            return $items;
        }

        foreach ($q->posts as $post) {
            if (!($post instanceof WP_Post)) {
                continue;
            }

            if (self::should_exclude_post_from_llms($post)) {
                continue;
            }

            $url = get_permalink($post);
            if (!$url) {
                continue;
            }

            $items[] = self::build_llms_item_from_post($post, 88);

            if (count($items) >= $limit) {
                break;
            }
        }

        wp_reset_postdata();
        return $items;
    }

    private static function get_top_terms($limit) {
        $items = array();
        $taxonomies = self::get_filterable_taxonomies();

        foreach ($taxonomies as $taxonomy => $taxonomy_obj) {
            $terms = get_terms(
                array(
                    'taxonomy' => $taxonomy,
                    'hide_empty' => true,
                    'number' => max(1, (int) $limit),
                    'orderby' => 'count',
                    'order' => 'DESC',
                )
            );

            if (is_wp_error($terms) || empty($terms)) {
                continue;
            }

            foreach ($terms as $term) {
                $url = get_term_link($term);
                if (is_wp_error($url)) {
                    continue;
                }
                $items[] = array(
                    'name' => $taxonomy_obj->labels && !empty($taxonomy_obj->labels->singular_name) ? $taxonomy_obj->labels->singular_name . ': ' . $term->name : $term->name,
                    'url' => $url,
                    'count' => (int) $term->count,
                );
            }
        }

        usort(
            $items,
            function ($a, $b) {
                return (int) $b['count'] - (int) $a['count'];
            }
        );

        return array_slice($items, 0, max(1, (int) $limit));
    }

    private static function build_llms_txt($site_name, $site_desc, $site_url, $locale, array $featured_items, array $core_pages, array $recent_posts, array $topics) {
        $lines = array();
        $lines[] = '# ' . self::safe_line($site_name . '｜LLMS 内容索引');
        $lines[] = '> ' . self::safe_line($site_desc);
        $lines[] = '';

        $lines[] = '## Core Pages';
        foreach ($core_pages as $item) {
            $lines[] = '- [' . self::safe_line($item['title']) . '](' . esc_url_raw($item['url']) . '): ' . self::safe_line($item['desc']);
        }
        $lines[] = '';

        if (!empty($featured_items)) {
            $lines[] = '## Featured Content';
            foreach ($featured_items as $item) {
                $lines[] = '- [' . self::safe_line($item['title']) . '](' . esc_url_raw($item['url']) . '): ' . self::safe_line($item['desc']);
            }
            $lines[] = '';
        }

        $lines[] = '## Latest Content';
        foreach ($recent_posts as $item) {
            $lines[] = '- [' . self::safe_line($item['title']) . '](' . esc_url_raw($item['url']) . '): ' . self::safe_line($item['desc']);
        }
        $lines[] = '';

        if (!empty($topics)) {
            $lines[] = '## Topics';
            foreach ($topics as $topic) {
                $lines[] = '- [' . self::safe_line($topic['name']) . '](' . esc_url_raw($topic['url']) . '): ' . $topic['count'] . ' 篇内容。';
            }
            $lines[] = '';
        }

        $lines[] = '## Key Facts';
        $lines[] = '- Site: ' . esc_url_raw($site_url);
        $lines[] = '- Locale: ' . self::safe_line($locale);
        $lines[] = '- Updated: ' . gmdate('Y-m-d');
        $lines[] = '';

        $lines[] = '## Contact';
        $lines[] = '- Website: ' . esc_url_raw($site_url);
        $lines[] = '';

        return implode("\n", $lines);
    }

    private static function build_llms_full_txt($site_name, $site_desc, $site_url, $locale, array $featured_items, array $core_pages, array $recent_posts, array $topics) {
        $lines = array();
        $lines[] = '# ' . self::safe_line($site_name . '｜LLMS 扩展索引');
        $lines[] = '> ' . self::safe_line($site_desc);
        $lines[] = '';

        $lines[] = '## Site Identity';
        $lines[] = '- [站点首页](' . esc_url_raw($site_url) . '): 品牌入口与导航。';
        $lines[] = '';

        if (!empty($featured_items)) {
            $lines[] = '## Featured Content';
            foreach ($featured_items as $item) {
                $lines[] = '- [' . self::safe_line($item['title']) . '](' . esc_url_raw($item['url']) . '): ' . self::safe_line($item['desc']);
            }
            $lines[] = '';
        }

        $lines[] = '## Core Pages';
        foreach ($core_pages as $item) {
            $lines[] = '- [' . self::safe_line($item['title']) . '](' . esc_url_raw($item['url']) . '): ' . self::safe_line($item['desc']);
        }
        $lines[] = '';

        $lines[] = '## Recent Content';
        foreach ($recent_posts as $item) {
            $meta = $item['date'];
            if (!empty($item['cats'])) {
                $meta .= ' | ' . $item['cats'];
            }
            $lines[] = '- [' . self::safe_line($item['title']) . '](' . esc_url_raw($item['url']) . '): ' . self::safe_line($item['desc']) . '（' . self::safe_line($meta) . '）';
        }
        $lines[] = '';

        if (!empty($topics)) {
            $lines[] = '## Topic Index';
            foreach ($topics as $topic) {
                $lines[] = '- [' . self::safe_line($topic['name']) . '](' . esc_url_raw($topic['url']) . '): ' . $topic['count'] . ' 篇。';
            }
            $lines[] = '';
        }

        $lines[] = '## Key Facts';
        $lines[] = '- Site: ' . esc_url_raw($site_url);
        $lines[] = '- Locale: ' . self::safe_line($locale);
        $lines[] = '- Generated: ' . gmdate('Y-m-d H:i:s') . ' UTC';
        $lines[] = '';

        $lines[] = '## Contact';
        $lines[] = '- Website: ' . esc_url_raw($site_url);
        $lines[] = '';

        return implode("\n", $lines);
    }

    private static function dedupe_items_by_url(array $items, array &$seen_urls) {
        $unique = array();

        foreach ($items as $item) {
            if (empty($item['url'])) {
                continue;
            }

            $key = untrailingslashit((string) $item['url']);
            if (isset($seen_urls[$key])) {
                continue;
            }

            $seen_urls[$key] = true;
            $unique[] = $item;
        }

        return $unique;
    }

    private static function get_pinned_items($limit) {
        $settings = self::get_settings();
        $items = array();
        $seen_post_ids = array();

        foreach (self::get_ref_lines(isset($settings['pinned_refs']) ? $settings['pinned_refs'] : '') as $ref) {
            $post = self::resolve_ref_to_post($ref);
            if (!$post instanceof WP_Post || isset($seen_post_ids[$post->ID]) || self::should_exclude_post_from_llms($post)) {
                continue;
            }

            $seen_post_ids[$post->ID] = true;
            $items[] = self::build_llms_item_from_post($post, 96);
            if (count($items) >= $limit) {
                return $items;
            }
        }

        $query = new WP_Query(
            array(
                'post_type' => array_keys(self::get_available_llms_post_types()),
                'post_status' => 'publish',
                'posts_per_page' => max(1, (int) $limit * 3),
                'orderby' => 'date',
                'order' => 'DESC',
                'meta_query' => array(
                    array(
                        'key' => self::META_PIN_KEY,
                        'value' => '1',
                    ),
                ),
                'no_found_rows' => true,
            )
        );

        foreach ($query->posts as $post) {
            if (!($post instanceof WP_Post) || isset($seen_post_ids[$post->ID]) || self::should_exclude_post_from_llms($post)) {
                continue;
            }

            $seen_post_ids[$post->ID] = true;
            $items[] = self::build_llms_item_from_post($post, 96);

            if (count($items) >= $limit) {
                break;
            }
        }

        wp_reset_postdata();
        return $items;
    }

    private static function build_llms_item_from_post($post, $max_len) {
        if (!$post instanceof WP_Post) {
            return array();
        }

        return array(
            'title' => get_the_title($post),
            'url' => get_permalink($post),
            'desc' => self::get_llms_description_for_post($post, $max_len),
            'date' => get_the_date('Y-m-d', $post),
            'cats' => implode(' / ', self::get_term_labels_for_post($post, 3)),
        );
    }

    private static function get_llms_description_for_post($post, $max_len) {
        if (!$post instanceof WP_Post) {
            return '内容摘要待补充。';
        }

        $custom = trim((string) get_post_meta($post->ID, self::META_SUMMARY_KEY, true));
        if ($custom !== '') {
            return self::clean_excerpt($custom, $max_len);
        }

        $excerpt_source = get_the_excerpt($post);
        if (!$excerpt_source) {
            $excerpt_source = $post->post_excerpt ? $post->post_excerpt : $post->post_content;
        }

        return self::clean_excerpt($excerpt_source, $max_len);
    }

    private static function build_tax_query_for_settings($settings = null) {
        $selected = self::get_selected_term_keys($settings);
        if (empty($selected)) {
            return array();
        }

        $grouped = array();
        foreach ($selected as $key) {
            list($taxonomy, $term_id) = explode(':', $key, 2);
            $taxonomy = sanitize_key($taxonomy);
            $term_id = absint($term_id);
            if ($taxonomy && $term_id > 0) {
                $grouped[$taxonomy][] = $term_id;
            }
        }

        if (empty($grouped)) {
            return array();
        }

        $tax_query = array('relation' => 'OR');
        foreach ($grouped as $taxonomy => $ids) {
            $tax_query[] = array(
                'taxonomy' => $taxonomy,
                'field' => 'term_id',
                'terms' => array_values(array_unique($ids)),
                'operator' => 'IN',
            );
        }

        return count($tax_query) > 1 ? $tax_query : array();
    }

    private static function get_term_labels_for_post($post, $limit) {
        if (!$post instanceof WP_Post) {
            return array();
        }

        $labels = array();
        $taxonomies = self::get_filterable_taxonomies(array($post->post_type));

        foreach ($taxonomies as $taxonomy => $obj) {
            $terms = wp_get_post_terms($post->ID, $taxonomy, array('fields' => 'names'));
            if (is_wp_error($terms) || empty($terms)) {
                continue;
            }

            foreach ($terms as $term_name) {
                if (!in_array($term_name, $labels, true)) {
                    $labels[] = $term_name;
                }

                if (count($labels) >= $limit) {
                    return $labels;
                }
            }
        }

        return $labels;
    }

    private static function resolve_ref_to_post($ref) {
        $ref = trim((string) $ref);
        if ($ref === '') {
            return null;
        }

        if (ctype_digit($ref)) {
            $post = get_post(absint($ref));
            return $post instanceof WP_Post ? $post : null;
        }

        if (strpos($ref, 'http://') === 0 || strpos($ref, 'https://') === 0) {
            $post_id = url_to_postid($ref);
            if ($post_id) {
                return get_post($post_id);
            }
        }

        if (strpos($ref, '/') === 0) {
            $post_id = url_to_postid(home_url($ref));
            if ($post_id) {
                return get_post($post_id);
            }
        }

        return self::find_post_by_slug($ref);
    }

    private static function find_post_by_slug($slug) {
        $slug = sanitize_title($slug);
        if ($slug === '') {
            return null;
        }

        $posts = get_posts(
            array(
                'name' => $slug,
                'post_type' => array_keys(self::get_available_llms_post_types()),
                'post_status' => 'publish',
                'posts_per_page' => 1,
                'no_found_rows' => true,
            )
        );

        return !empty($posts[0]) && $posts[0] instanceof WP_Post ? $posts[0] : null;
    }

    private static function write_root_file($file_name, $content) {
        $path = trailingslashit(ABSPATH) . ltrim($file_name, '/');
        return (bool) @file_put_contents($path, $content, LOCK_EX);
    }

    private static function clean_excerpt($text, $max_len) {
        $text = html_entity_decode((string) $text, ENT_QUOTES, 'UTF-8');
        $text = strip_shortcodes($text);
        $text = wp_strip_all_tags($text, true);
        $text = preg_replace('/\s+/u', ' ', trim($text));

        if ($text === '') {
            return '内容摘要待补充。';
        }

        if (function_exists('mb_strlen') && function_exists('mb_substr')) {
            if (mb_strlen($text, 'UTF-8') > $max_len) {
                $text = mb_substr($text, 0, $max_len, 'UTF-8') . '…';
            }
        } elseif (strlen($text) > $max_len) {
            $text = substr($text, 0, $max_len) . '...';
        }

        return $text;
    }

    private static function safe_line($text) {
        $text = str_replace(array("\r", "\n"), ' ', (string) $text);
        return trim(preg_replace('/\s+/u', ' ', $text));
    }

    private static function get_default_settings() {
        return array(
            'management_capability' => 'manage_options',
            'logging_enabled' => 1,
            'cleanup_on_uninstall' => 0,
            'exclude_low_value_from_llms' => 1,
            'enable_low_value_noindex' => 0,
            'enable_llms_link_tag' => 1,
            'enable_wp_endpoint_fix' => 0,
            'safe_fix_mode' => 'strict',
            'enable_fallback_social_meta' => 0,
            'enable_fallback_schema_markup' => 0,
            'included_post_types' => array('post'),
            'included_term_keys' => array(),
            'pinned_refs' => '',
            'excluded_refs' => '',
            'scheduled_scan_enabled' => 0,
            'scheduled_scan_frequency' => 'weekly',
            'scheduled_scan_weekday' => 'mon',
            'scheduled_scan_hour' => 9,
            'auto_safe_fix_enabled' => 0,
            'auto_safe_fix_on_manual_scan' => 0,
            'scan_history_limit' => 20,
            'notify_on_warn' => 1,
            'notify_on_fail' => 1,
            'notify_on_manual_scan' => 0,
            'notify_email_enabled' => 0,
            'notification_email' => get_option('admin_email'),
            'notify_webhook_enabled' => 0,
            'notification_webhook_url' => '',
            'notification_email_subject_template' => self::get_default_email_subject_template(),
            'notification_email_body_template' => self::get_default_email_body_template(),
            'notification_webhook_template' => self::get_default_webhook_template(),
            'cache_purge_enabled' => 0,
            'cache_purge_local_enabled' => 1,
            'cache_purge_cloudflare_enabled' => 0,
            'cache_purge_cloudflare_zone_id' => '',
            'cache_purge_cloudflare_api_token' => '',
            'cache_purge_cloudflare_mode' => 'selected',
            'cache_purge_additional_urls' => '',
            'organization_logo_url' => '',
            'organization_sameas' => '',
        );
    }

    private static function get_settings() {
        $saved = get_option(self::SETTINGS_OPTION_KEY, array());
        if (!is_array($saved)) {
            $saved = array();
        }
        return wp_parse_args($saved, self::get_default_settings());
    }

    private static function save_settings(array $settings) {
        update_option(self::SETTINGS_OPTION_KEY, $settings, false);
    }

    private static function sanitize_settings_input($input) {
        $settings = self::get_default_settings();
        $settings['management_capability'] = self::sanitize_management_capability(isset($input['management_capability']) ? $input['management_capability'] : 'manage_options');
        $settings['logging_enabled'] = !empty($input['logging_enabled']) ? 1 : 0;
        $settings['cleanup_on_uninstall'] = !empty($input['cleanup_on_uninstall']) ? 1 : 0;
        $settings['exclude_low_value_from_llms'] = !empty($input['exclude_low_value_from_llms']) ? 1 : 0;
        $settings['enable_low_value_noindex'] = !empty($input['enable_low_value_noindex']) ? 1 : 0;
        $settings['enable_llms_link_tag'] = !empty($input['enable_llms_link_tag']) ? 1 : 0;
        $settings['enable_wp_endpoint_fix'] = !empty($input['enable_wp_endpoint_fix']) ? 1 : 0;
        $settings['safe_fix_mode'] = self::sanitize_safe_fix_mode(isset($input['safe_fix_mode']) ? $input['safe_fix_mode'] : 'strict');
        $settings['enable_fallback_social_meta'] = !empty($input['enable_fallback_social_meta']) ? 1 : 0;
        $settings['enable_fallback_schema_markup'] = !empty($input['enable_fallback_schema_markup']) ? 1 : 0;
        $settings['included_post_types'] = self::sanitize_selected_post_types(isset($input['included_post_types']) ? $input['included_post_types'] : array());
        $settings['included_term_keys'] = self::sanitize_selected_term_keys(isset($input['included_term_keys']) ? $input['included_term_keys'] : array(), $settings['included_post_types']);
        $settings['pinned_refs'] = self::sanitize_ref_lines(isset($input['pinned_refs']) ? $input['pinned_refs'] : '');
        $settings['excluded_refs'] = self::sanitize_ref_lines(isset($input['excluded_refs']) ? $input['excluded_refs'] : '');
        $settings['scheduled_scan_enabled'] = !empty($input['scheduled_scan_enabled']) ? 1 : 0;
        $settings['scheduled_scan_frequency'] = self::sanitize_schedule_frequency(isset($input['scheduled_scan_frequency']) ? $input['scheduled_scan_frequency'] : 'weekly');
        $settings['scheduled_scan_weekday'] = self::sanitize_schedule_weekday(isset($input['scheduled_scan_weekday']) ? $input['scheduled_scan_weekday'] : 'mon');
        $settings['scheduled_scan_hour'] = self::sanitize_schedule_hour(isset($input['scheduled_scan_hour']) ? $input['scheduled_scan_hour'] : 9);
        $settings['auto_safe_fix_enabled'] = !empty($input['auto_safe_fix_enabled']) ? 1 : 0;
        $settings['auto_safe_fix_on_manual_scan'] = !empty($input['auto_safe_fix_on_manual_scan']) ? 1 : 0;
        $settings['scan_history_limit'] = self::sanitize_history_limit(isset($input['scan_history_limit']) ? $input['scan_history_limit'] : 20);
        $settings['notify_on_warn'] = !empty($input['notify_on_warn']) ? 1 : 0;
        $settings['notify_on_fail'] = !empty($input['notify_on_fail']) ? 1 : 0;
        $settings['notify_on_manual_scan'] = !empty($input['notify_on_manual_scan']) ? 1 : 0;
        $settings['notify_email_enabled'] = !empty($input['notify_email_enabled']) ? 1 : 0;
        $settings['notification_email'] = self::sanitize_notification_email(isset($input['notification_email']) ? $input['notification_email'] : '');
        $settings['notify_webhook_enabled'] = !empty($input['notify_webhook_enabled']) ? 1 : 0;
        $settings['notification_webhook_url'] = self::sanitize_single_url(isset($input['notification_webhook_url']) ? $input['notification_webhook_url'] : '');
        $settings['notification_email_subject_template'] = self::sanitize_template_text(isset($input['notification_email_subject_template']) ? $input['notification_email_subject_template'] : self::get_default_email_subject_template(), false);
        $settings['notification_email_body_template'] = self::sanitize_template_text(isset($input['notification_email_body_template']) ? $input['notification_email_body_template'] : self::get_default_email_body_template(), true);
        $settings['notification_webhook_template'] = self::sanitize_template_text(isset($input['notification_webhook_template']) ? $input['notification_webhook_template'] : self::get_default_webhook_template(), true);
        $settings['cache_purge_enabled'] = !empty($input['cache_purge_enabled']) ? 1 : 0;
        $settings['cache_purge_local_enabled'] = !empty($input['cache_purge_local_enabled']) ? 1 : 0;
        $settings['cache_purge_cloudflare_enabled'] = !empty($input['cache_purge_cloudflare_enabled']) ? 1 : 0;
        $settings['cache_purge_cloudflare_zone_id'] = self::sanitize_cloudflare_zone_id(isset($input['cache_purge_cloudflare_zone_id']) ? $input['cache_purge_cloudflare_zone_id'] : '');
        $settings['cache_purge_cloudflare_api_token'] = self::sanitize_api_token(isset($input['cache_purge_cloudflare_api_token']) ? $input['cache_purge_cloudflare_api_token'] : '');
        $settings['cache_purge_cloudflare_mode'] = self::sanitize_cache_purge_mode(isset($input['cache_purge_cloudflare_mode']) ? $input['cache_purge_cloudflare_mode'] : 'selected');
        $settings['cache_purge_additional_urls'] = self::sanitize_url_lines(isset($input['cache_purge_additional_urls']) ? $input['cache_purge_additional_urls'] : '');
        $settings['organization_logo_url'] = self::sanitize_single_url(isset($input['organization_logo_url']) ? $input['organization_logo_url'] : '');
        $settings['organization_sameas'] = self::sanitize_sameas_links(isset($input['organization_sameas']) ? $input['organization_sameas'] : '');
        return $settings;
    }

    private static function sanitize_management_capability($value) {
        $value = sanitize_key($value);
        $allowed = array('manage_options', 'edit_pages', 'publish_posts');
        return in_array($value, $allowed, true) ? $value : 'manage_options';
    }

    private static function sanitize_single_url($url) {
        $url = trim((string) $url);
        if ($url === '') {
            return '';
        }
        $sanitized = esc_url_raw($url, array('http', 'https'));
        return $sanitized ? $sanitized : '';
    }

    private static function sanitize_sameas_links($raw) {
        $raw = str_replace("\r", "\n", (string) $raw);
        $parts = preg_split('/[\n,]+/', $raw);
        $clean = array();

        foreach ($parts as $part) {
            $url = self::sanitize_single_url($part);
            if ($url && !in_array($url, $clean, true)) {
                $clean[] = $url;
            }
        }

        return implode("\n", $clean);
    }

    private static function sanitize_url_lines($raw) {
        $raw = str_replace("\r", "\n", (string) $raw);
        $parts = preg_split('/\n+/', $raw);
        $clean = array();

        foreach ($parts as $part) {
            $part = trim((string) $part);
            if ($part === '') {
                continue;
            }

            if (strpos($part, '/') === 0) {
                $part = self::normalize_path($part);
            } else {
                $part = self::sanitize_single_url($part);
            }

            if ($part !== '' && !in_array($part, $clean, true)) {
                $clean[] = $part;
            }
        }

        return implode("\n", $clean);
    }

    private static function sanitize_api_token($value) {
        return trim(sanitize_text_field((string) $value));
    }

    private static function sanitize_cloudflare_zone_id($value) {
        $value = preg_replace('/[^a-zA-Z0-9]/', '', (string) $value);
        return trim($value);
    }

    private static function sanitize_cache_purge_mode($value) {
        $value = sanitize_key((string) $value);
        return in_array($value, array('selected', 'everything'), true) ? $value : 'selected';
    }

    private static function sanitize_safe_fix_mode($value) {
        $value = sanitize_key((string) $value);
        return in_array($value, array('strict', 'balanced'), true) ? $value : 'strict';
    }

    private static function sanitize_template_text($value, $multiline) {
        $value = (string) $value;
        $value = str_replace("\r\n", "\n", $value);
        $value = str_replace("\r", "\n", $value);

        if (!$multiline) {
            $value = str_replace("\n", ' ', $value);
        }

        return trim($value);
    }

    private static function sanitize_schedule_frequency($value) {
        $value = sanitize_key($value);
        return in_array($value, array('daily', 'weekly'), true) ? $value : 'weekly';
    }

    private static function sanitize_schedule_weekday($value) {
        $value = sanitize_key($value);
        return in_array($value, array('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'), true) ? $value : 'mon';
    }

    private static function sanitize_schedule_hour($value) {
        $hour = (int) $value;
        if ($hour < 0) {
            $hour = 0;
        }
        if ($hour > 23) {
            $hour = 23;
        }
        return $hour;
    }

    private static function sanitize_history_limit($value) {
        $limit = (int) $value;
        if ($limit < 5) {
            $limit = 5;
        }
        if ($limit > 100) {
            $limit = 100;
        }
        return $limit;
    }

    private static function sanitize_notification_email($value) {
        $email = sanitize_email((string) $value);
        if ($email === '') {
            $email = sanitize_email((string) get_option('admin_email'));
        }
        return $email;
    }

    private static function sanitize_ref_lines($raw) {
        $raw = str_replace("\r", "\n", (string) $raw);
        $parts = preg_split('/\n+/', $raw);
        $clean = array();

        foreach ($parts as $part) {
            $normalized = self::normalize_ref_value($part);
            if ($normalized !== '' && !in_array($normalized, $clean, true)) {
                $clean[] = $normalized;
            }
        }

        return implode("\n", $clean);
    }

    private static function normalize_ref_value($value) {
        $value = trim((string) $value);
        if ($value === '') {
            return '';
        }

        if (ctype_digit($value)) {
            return (string) absint($value);
        }

        if (strpos($value, 'http://') === 0 || strpos($value, 'https://') === 0) {
            return self::sanitize_single_url($value);
        }

        if (strpos($value, '/') === 0) {
            return '/' . ltrim(self::normalize_path($value), '/');
        }

        return sanitize_title($value);
    }

    private static function get_ref_lines($raw) {
        if (!is_string($raw) || trim($raw) === '') {
            return array();
        }

        return array_values(array_filter(array_map('trim', explode("\n", str_replace("\r", "\n", $raw)))));
    }

    private static function sanitize_selected_post_types($input) {
        $available = self::get_available_llms_post_types();
        $allowed = array_keys($available);
        $selected = array();

        if (is_string($input)) {
            $input = array($input);
        }

        if (!is_array($input)) {
            $input = array();
        }

        foreach ($input as $post_type) {
            $post_type = sanitize_key($post_type);
            if (in_array($post_type, $allowed, true) && !in_array($post_type, $selected, true)) {
                $selected[] = $post_type;
            }
        }

        if (empty($selected)) {
            $selected[] = 'post';
        }

        return $selected;
    }

    private static function get_available_llms_post_types() {
        $types = get_post_types(array('publicly_queryable' => true), 'objects');
        $items = array();
        $excluded = array('attachment', 'revision', 'nav_menu_item', 'custom_css', 'customize_changeset', 'oembed_cache', 'user_request');

        foreach ($types as $post_type => $obj) {
            if (in_array($post_type, $excluded, true)) {
                continue;
            }

            if (empty($obj->show_ui)) {
                continue;
            }

            $items[$post_type] = $obj;
        }

        return $items;
    }

    private static function get_included_post_types($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        $selected = isset($settings['included_post_types']) && is_array($settings['included_post_types']) ? $settings['included_post_types'] : array();
        return self::sanitize_selected_post_types($selected);
    }

    private static function get_filterable_taxonomies($post_types = null) {
        if (!is_array($post_types) || empty($post_types)) {
            $post_types = self::get_included_post_types();
        }

        $items = array();
        $excluded = array('post_format', 'language', 'term_language', 'post_translations');

        foreach ($post_types as $post_type) {
            $taxonomies = get_object_taxonomies($post_type, 'objects');
            foreach ($taxonomies as $taxonomy => $obj) {
                if (in_array($taxonomy, $excluded, true)) {
                    continue;
                }

                if (empty($obj->public) || empty($obj->show_ui) || empty($obj->hierarchical)) {
                    continue;
                }

                $items[$taxonomy] = $obj;
            }
        }

        return $items;
    }

    private static function get_available_filter_terms($post_types = null) {
        $taxonomies = self::get_filterable_taxonomies($post_types);
        $items = array();

        foreach ($taxonomies as $taxonomy => $obj) {
            $terms = get_terms(
                array(
                    'taxonomy' => $taxonomy,
                    'hide_empty' => false,
                    'orderby' => 'count',
                    'order' => 'DESC',
                    'number' => 100,
                )
            );

            if (is_wp_error($terms) || empty($terms)) {
                continue;
            }

            $items[$taxonomy] = array(
                'label' => $obj->labels && !empty($obj->labels->name) ? $obj->labels->name : $taxonomy,
                'terms' => $terms,
            );
        }

        return $items;
    }

    private static function sanitize_selected_term_keys($input, $post_types) {
        $available = self::get_available_filter_terms($post_types);
        $selected = array();

        if (is_string($input)) {
            $input = array($input);
        }

        if (!is_array($input)) {
            $input = array();
        }

        foreach ($input as $raw_key) {
            $raw_key = trim((string) $raw_key);
            if ($raw_key === '' || strpos($raw_key, ':') === false) {
                continue;
            }

            list($taxonomy, $term_id) = array_map('trim', explode(':', $raw_key, 2));
            $taxonomy = sanitize_key($taxonomy);
            $term_id = absint($term_id);

            if ($term_id < 1 || empty($available[$taxonomy])) {
                continue;
            }

            foreach ($available[$taxonomy]['terms'] as $term) {
                if ((int) $term->term_id === $term_id) {
                    $selected[] = $taxonomy . ':' . $term_id;
                    break;
                }
            }
        }

        return array_values(array_unique($selected));
    }

    private static function get_selected_term_keys($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        return isset($settings['included_term_keys']) && is_array($settings['included_term_keys']) ? array_values(array_unique($settings['included_term_keys'])) : array();
    }

    private static function get_sameas_links($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }
        $raw = isset($settings['organization_sameas']) ? $settings['organization_sameas'] : '';
        if ($raw === '') {
            return array();
        }
        return array_values(array_filter(array_map('trim', explode("\n", $raw))));
    }

    private static function get_default_email_subject_template() {
        return '[GEO Alert] {{site_name}} - {{overall_status}} (Fail {{fail_count}} / Warn {{warn_count}})';
    }

    private static function get_default_email_body_template() {
        return "Site: {{site_name}}\nURL: {{site_url}}\nTime: {{scan_time}}\nTrigger: {{trigger}}\nSummary: {{summary}}\nTrend: {{trend}}\n\nIssues:\n{{issues}}\n\nRecommendations:\n{{recommendations}}\n";
    }

    private static function get_default_webhook_template() {
        return "{\n  \"site_name\": {{site_name_json}},\n  \"site_url\": {{site_url_json}},\n  \"scan_time\": {{scan_time_json}},\n  \"trigger\": {{trigger_json}},\n  \"overall_status\": {{overall_status_json}},\n  \"summary\": {{summary_json}},\n  \"trend\": {{trend_json}},\n  \"issues\": {{issues_json}},\n  \"recommendations\": {{recommendations_json}}\n}";
    }

    private static function set_notice($type, $message) {
        update_option(
            self::NOTICE_OPTION_KEY,
            array(
                'type' => $type,
                'message' => $message,
            ),
            false
        );
    }

    private static function consume_notice() {
        $notice = get_option(self::NOTICE_OPTION_KEY, array());
        delete_option(self::NOTICE_OPTION_KEY);
        return is_array($notice) ? $notice : array();
    }

    private static function get_fix_preview() {
        $preview = get_option(self::PREVIEW_OPTION_KEY, array());
        return is_array($preview) ? $preview : array();
    }

    private static function save_fix_preview(array $preview) {
        update_option(self::PREVIEW_OPTION_KEY, $preview, false);
    }

    private static function clear_fix_preview() {
        delete_option(self::PREVIEW_OPTION_KEY);
    }

    private static function backup_current_settings() {
        update_option(
            self::SETTINGS_BACKUP_OPTION_KEY,
            array(
                'time' => current_time('mysql'),
                'settings' => self::get_settings(),
            ),
            false
        );
    }

    private static function get_settings_backup() {
        $backup = get_option(self::SETTINGS_BACKUP_OPTION_KEY, array());
        return is_array($backup) ? $backup : array();
    }

    private static function get_scan_history() {
        $history = get_option(self::SCAN_HISTORY_OPTION_KEY, array());
        return is_array($history) ? array_values($history) : array();
    }

    private static function save_scan_history(array $history, $limit = null) {
        if ($limit === null) {
            $settings = self::get_settings();
            $limit = isset($settings['scan_history_limit']) ? (int) $settings['scan_history_limit'] : 20;
        }

        $limit = self::sanitize_history_limit($limit);
        $history = array_slice(array_values($history), 0, $limit);
        update_option(self::SCAN_HISTORY_OPTION_KEY, $history, false);
    }

    private static function get_management_capability() {
        $settings = self::get_settings();
        return self::sanitize_management_capability(isset($settings['management_capability']) ? $settings['management_capability'] : 'manage_options');
    }

    private static function get_admin_page_url() {
        return self::get_management_capability() === 'manage_options'
            ? admin_url('options-general.php?page=' . self::ADMIN_SLUG)
            : admin_url('admin.php?page=' . self::ADMIN_SLUG);
    }

    private static function get_logs() {
        $logs = get_option(self::LOG_OPTION_KEY, array());
        return is_array($logs) ? array_values($logs) : array();
    }

    private static function clear_logs() {
        delete_option(self::LOG_OPTION_KEY);
    }

    private static function log_event($level, $message, array $context = array(), $force = false) {
        $settings = self::get_settings();
        if (!$force && empty($settings['logging_enabled'])) {
            return;
        }

        $logs = self::get_logs();
        array_unshift(
            $logs,
            array(
                'time' => current_time('mysql'),
                'level' => sanitize_key($level ?: 'info'),
                'message' => sanitize_text_field((string) $message),
                'context' => !empty($context) ? wp_json_encode($context, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) : '',
            )
        );

        $logs = array_slice($logs, 0, 100);
        update_option(self::LOG_OPTION_KEY, $logs, false);
    }

    private static function get_cache_queue() {
        $queue = get_option(self::CACHE_QUEUE_OPTION_KEY, array());
        return is_array($queue) ? array_values($queue) : array();
    }

    private static function save_cache_queue(array $queue) {
        update_option(self::CACHE_QUEUE_OPTION_KEY, array_values(array_unique(array_filter($queue))), false);
    }

    private static function queue_cache_urls(array $urls) {
        if (empty($urls)) {
            return;
        }

        $queue = self::get_cache_queue();
        foreach ($urls as $url) {
            $normalized = self::normalize_cache_url($url);
            if ($normalized !== '' && !in_array($normalized, $queue, true)) {
                $queue[] = $normalized;
            }
        }
        self::save_cache_queue($queue);
    }

    private static function consume_cache_queue() {
        $queue = self::get_cache_queue();
        delete_option(self::CACHE_QUEUE_OPTION_KEY);
        return $queue;
    }

    private static function normalize_cache_url($url) {
        $url = trim((string) $url);
        if ($url === '') {
            return '';
        }

        if (strpos($url, '/') === 0) {
            $url = home_url(self::normalize_path($url));
        }

        $url = self::sanitize_single_url($url);
        if ($url === '') {
            return '';
        }

        return untrailingslashit($url) === untrailingslashit(home_url('/')) ? trailingslashit(home_url('/')) : $url;
    }

    private static function get_post_related_cache_urls($post) {
        if (!$post instanceof WP_Post) {
            return array();
        }

        $urls = array(
            home_url('/'),
        );

        $permalink = get_permalink($post);
        if ($permalink) {
            $urls[] = $permalink;
        }

        $author_url = get_author_posts_url($post->post_author);
        if ($author_url) {
            $urls[] = $author_url;
        }

        $front_page_id = absint(get_option('page_on_front'));
        if ($front_page_id) {
            $front_url = get_permalink($front_page_id);
            if ($front_url) {
                $urls[] = $front_url;
            }
        }

        $posts_page_id = absint(get_option('page_for_posts'));
        if ($posts_page_id) {
            $posts_url = get_permalink($posts_page_id);
            if ($posts_url) {
                $urls[] = $posts_url;
            }
        }

        foreach (get_object_taxonomies($post->post_type) as $taxonomy) {
            $terms = wp_get_post_terms($post->ID, $taxonomy);
            if (is_wp_error($terms) || empty($terms)) {
                continue;
            }

            foreach ($terms as $term) {
                $term_url = get_term_link($term);
                if (!is_wp_error($term_url) && $term_url) {
                    $urls[] = $term_url;
                }
            }
        }

        return array_values(array_unique(array_map(array(__CLASS__, 'normalize_cache_url'), $urls)));
    }

    private static function get_cache_purge_urls(array $queued_urls, $settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        $urls = array(
            home_url('/'),
            home_url('/llms.txt'),
            home_url('/llms-full.txt'),
            home_url('/robots.txt'),
            home_url('/sitemap.xml'),
            home_url('/sitemap_index.xml'),
            home_url('/wp-sitemap.xml'),
        );

        $front_page_id = absint(get_option('page_on_front'));
        if ($front_page_id) {
            $front_url = get_permalink($front_page_id);
            if ($front_url) {
                $urls[] = $front_url;
            }
        }

        $posts_page_id = absint(get_option('page_for_posts'));
        if ($posts_page_id) {
            $posts_url = get_permalink($posts_page_id);
            if ($posts_url) {
                $urls[] = $posts_url;
            }
        }

        foreach ($queued_urls as $url) {
            $urls[] = $url;
        }

        foreach (self::get_ref_lines(isset($settings['cache_purge_additional_urls']) ? $settings['cache_purge_additional_urls'] : '') as $line) {
            $urls[] = self::normalize_cache_url($line);
        }

        $clean = array();
        foreach ($urls as $url) {
            $normalized = self::normalize_cache_url($url);
            if ($normalized !== '' && !in_array($normalized, $clean, true)) {
                $clean[] = $normalized;
            }
        }

        return $clean;
    }

    private static function purge_enabled_caches($reason = 'regenerate') {
        $queued_urls = self::consume_cache_queue();
        $settings = self::get_settings();
        $result = array(
            'time' => current_time('mysql'),
            'reason' => $reason,
            'status' => 'disabled',
            'urls' => self::get_cache_purge_urls($queued_urls, $settings),
            'details' => array(),
        );

        if ($reason === 'skip') {
            $result['status'] = 'skipped';
            $result['details'][] = 'llms 文件未成功写入，跳过缓存清理。';
            return $result;
        }

        if (empty($settings['cache_purge_enabled'])) {
            $result['details'][] = '缓存联动未启用。';
            return $result;
        }

        $providers = array();
        $statuses = array();

        if (!empty($settings['cache_purge_local_enabled'])) {
            $local_result = self::purge_local_page_cache();
            $providers['local'] = $local_result;
            $statuses[] = isset($local_result['status']) ? $local_result['status'] : 'info';
            if (!empty($local_result['summary'])) {
                $result['details'][] = '本地缓存：' . $local_result['summary'];
            }
        }

        if (!empty($settings['cache_purge_cloudflare_enabled'])) {
            $cloudflare_result = self::purge_cloudflare_cache($result['urls'], $settings);
            $providers['cloudflare'] = $cloudflare_result;
            $statuses[] = isset($cloudflare_result['status']) ? $cloudflare_result['status'] : 'info';
            if (!empty($cloudflare_result['summary'])) {
                $result['details'][] = 'Cloudflare：' . $cloudflare_result['summary'];
            }
        }

        if (empty($providers)) {
            $result['status'] = 'warn';
            $result['details'][] = '已启用缓存联动，但未启用任何缓存提供方。';
            return $result;
        }

        if (in_array('fail', $statuses, true)) {
            $result['status'] = 'fail';
        } elseif (in_array('warn', $statuses, true)) {
            $result['status'] = 'warn';
        } else {
            $result['status'] = 'pass';
        }

        $result['providers'] = $providers;
        return $result;
    }

    private static function purge_local_page_cache() {
        $handlers = array();

        if (function_exists('rocket_clean_domain')) {
            rocket_clean_domain();
            $handlers[] = 'WP Rocket';
        }

        if (function_exists('w3tc_flush_all')) {
            w3tc_flush_all();
            $handlers[] = 'W3 Total Cache';
        }

        if (function_exists('litespeed_purge_all')) {
            litespeed_purge_all();
            $handlers[] = 'LiteSpeed Cache';
        }

        if (function_exists('sg_cachepress_purge_cache')) {
            sg_cachepress_purge_cache();
            $handlers[] = 'SiteGround Optimizer';
        }

        if (function_exists('wpfc_clear_all_cache')) {
            wpfc_clear_all_cache(true);
            $handlers[] = 'WP Fastest Cache';
        }

        if (empty($handlers)) {
            return array(
                'status' => 'warn',
                'summary' => '未检测到可调用的本地页面缓存接口。',
            );
        }

        self::log_event('info', 'local_cache_purged', array('providers' => $handlers));
        return array(
            'status' => 'pass',
            'summary' => '已调用：' . implode(' / ', $handlers),
        );
    }

    private static function purge_cloudflare_cache(array $urls, array $settings) {
        $zone_id = isset($settings['cache_purge_cloudflare_zone_id']) ? trim((string) $settings['cache_purge_cloudflare_zone_id']) : '';
        $token = isset($settings['cache_purge_cloudflare_api_token']) ? trim((string) $settings['cache_purge_cloudflare_api_token']) : '';
        $mode = self::sanitize_cache_purge_mode(isset($settings['cache_purge_cloudflare_mode']) ? $settings['cache_purge_cloudflare_mode'] : 'selected');

        if ($zone_id === '' || $token === '') {
            return array(
                'status' => 'fail',
                'summary' => '缺少 Cloudflare Zone ID 或 API Token。',
            );
        }

        $payload = $mode === 'everything'
            ? array('purge_everything' => true)
            : array('files' => array_values(array_unique(array_filter($urls))));

        $response = wp_remote_post(
            'https://api.cloudflare.com/client/v4/zones/' . rawurlencode($zone_id) . '/purge_cache',
            array(
                'timeout' => 20,
                'headers' => array(
                    'Authorization' => 'Bearer ' . $token,
                    'Content-Type' => 'application/json; charset=utf-8',
                ),
                'body' => wp_json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            )
        );

        if (is_wp_error($response)) {
            self::log_event('error', 'cloudflare_cache_purge_failed', array('error' => $response->get_error_message()), true);
            return array(
                'status' => 'fail',
                'summary' => '请求 Cloudflare 失败：' . $response->get_error_message(),
            );
        }

        $status_code = (int) wp_remote_retrieve_response_code($response);
        $decoded = json_decode((string) wp_remote_retrieve_body($response), true);
        $success = $status_code >= 200 && $status_code < 300 && !empty($decoded['success']);

        if (!$success) {
            $errors = array();
            if (!empty($decoded['errors']) && is_array($decoded['errors'])) {
                foreach ($decoded['errors'] as $error) {
                    if (is_array($error) && !empty($error['message'])) {
                        $errors[] = $error['message'];
                    }
                }
            }
            self::log_event('error', 'cloudflare_cache_purge_failed', array('status_code' => $status_code, 'errors' => $errors), true);
            return array(
                'status' => 'fail',
                'summary' => 'Cloudflare 返回失败：HTTP ' . $status_code . (!empty($errors) ? '，' . implode(' / ', $errors) : ''),
            );
        }

        self::log_event('info', 'cloudflare_cache_purged', array('mode' => $mode, 'url_count' => $mode === 'everything' ? 0 : count($urls)));
        return array(
            'status' => 'pass',
            'summary' => $mode === 'everything'
                ? '已执行 purge everything。'
                : '已提交 ' . count($urls) . ' 个 URL 到 Cloudflare purge。'
        );
    }

    private static function get_safe_fix_plan($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        $integration = self::get_integration_context();
        $target = $settings;
        $changes = array();
        $skipped = array();
        $safe_fix_mode = self::sanitize_safe_fix_mode(isset($settings['safe_fix_mode']) ? $settings['safe_fix_mode'] : 'strict');

        $plans = array(
            'exclude_low_value_from_llms' => array(
                'label' => '低价值 llms 过滤',
                'target' => 1,
                'reason' => '减少无价值链接进入 llms 文件。',
            ),
            'enable_low_value_noindex' => array(
                'label' => '低价值页 noindex',
                'target' => 1,
                'reason' => '避免登录、找回密码、搜索页等干扰索引。',
            ),
            'enable_llms_link_tag' => array(
                'label' => '首页 LLMS Link',
                'target' => 1,
                'reason' => '在首页 head 声明 llms.txt 位置，便于爬虫发现。',
            ),
            'enable_wp_endpoint_fix' => array(
                'label' => 'WP 端点修复',
                'target' => 1,
                'reason' => '在 WP 层兜底 robots/sitemap 端点，降低 rewrite 冲突影响。',
            ),
        );

        if (empty($integration['seo_plugins']) && $safe_fix_mode === 'balanced') {
            $plans['enable_fallback_social_meta'] = array(
                'label' => '基础 OG / Twitter',
                'target' => 1,
                'reason' => '当前未检测到 SEO 插件，由本插件补基础社交标签。',
            );
            $plans['enable_fallback_schema_markup'] = array(
                'label' => '基础 Schema',
                'target' => 1,
                'reason' => '当前未检测到 SEO 插件，由本插件补基础结构化数据。',
            );
        } elseif ($safe_fix_mode !== 'balanced') {
            $skipped[] = '当前是严格安全模式（Strict），不会自动启用 OG/Twitter 与 Schema 补全。';
        } else {
            $skipped[] = '检测到 SEO 插件：' . implode(' / ', $integration['seo_plugins']) . '，预设不会强行打开基础 OG/Twitter 与 Schema。';
        }

        foreach ($plans as $key => $plan) {
            $current = !empty($settings[$key]) ? 1 : 0;
            $target[$key] = $plan['target'];
            if ($current !== (int) $plan['target']) {
                $changes[] = array(
                    'key' => $key,
                    'label' => $plan['label'],
                    'from' => $current,
                    'to' => (int) $plan['target'],
                    'reason' => $plan['reason'],
                );
            }
        }

        return array(
            'time' => current_time('mysql'),
            'changes' => $changes,
            'skipped' => $skipped,
            'target_settings' => $target,
            'summary' => empty($changes) ? '当前没有待应用的预设安全修复。' : '预计会变更 ' . count($changes) . ' 项设置。',
        );
    }

    private static function get_overall_scan_status(array $summary) {
        if (!empty($summary['fail'])) {
            return 'fail';
        }
        if (!empty($summary['warn'])) {
            return 'warn';
        }
        return 'pass';
    }

    private static function get_next_scheduled_scan_timestamp($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        if (empty($settings['scheduled_scan_enabled'])) {
            return 0;
        }

        $tz = wp_timezone();
        $now = new DateTime('now', $tz);
        $target = clone $now;
        $hour = self::sanitize_schedule_hour(isset($settings['scheduled_scan_hour']) ? $settings['scheduled_scan_hour'] : 9);
        $target->setTime($hour, 0, 0);

        if ((isset($settings['scheduled_scan_frequency']) ? $settings['scheduled_scan_frequency'] : 'weekly') === 'daily') {
            if ($target <= $now) {
                $target->modify('+1 day');
            }
            return $target->getTimestamp();
        }

        $weekday = self::sanitize_schedule_weekday(isset($settings['scheduled_scan_weekday']) ? $settings['scheduled_scan_weekday'] : 'mon');
        $map = array('sun' => 0, 'mon' => 1, 'tue' => 2, 'wed' => 3, 'thu' => 4, 'fri' => 5, 'sat' => 6);
        $current_weekday = (int) $now->format('w');
        $target_weekday = $map[$weekday];
        $offset = ($target_weekday - $current_weekday + 7) % 7;

        if ($offset === 0 && $target <= $now) {
            $offset = 7;
        }

        if ($offset > 0) {
            $target->modify('+' . $offset . ' days');
        }

        return $target->getTimestamp();
    }

    public static function register_cron_schedules($schedules) {
        if (!isset($schedules['geo_llms_weekly'])) {
            $schedules['geo_llms_weekly'] = array(
                'interval' => WEEK_IN_SECONDS,
                'display' => 'Once Weekly',
            );
        }

        return $schedules;
    }

    private static function sync_scan_schedule($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        wp_clear_scheduled_hook(self::SCAN_CRON_HOOK);

        if (empty($settings['scheduled_scan_enabled'])) {
            return;
        }

        $timestamp = self::get_next_scheduled_scan_timestamp($settings);
        if ($timestamp <= 0) {
            return;
        }

        $recurrence = (isset($settings['scheduled_scan_frequency']) ? $settings['scheduled_scan_frequency'] : 'weekly') === 'daily' ? 'daily' : 'geo_llms_weekly';
        wp_schedule_event($timestamp, $recurrence, self::SCAN_CRON_HOOK);
    }

    public static function register_meta_boxes() {
        foreach (array_keys(self::get_available_llms_post_types()) as $post_type) {
            add_meta_box(
                'geo-llms-meta-box',
                'GEO LLMS',
                array(__CLASS__, 'render_post_meta_box'),
                $post_type,
                'side',
                'default'
            );
        }
    }

    public static function render_post_meta_box($post) {
        if (!($post instanceof WP_Post)) {
            return;
        }

        wp_nonce_field('geo_llms_post_meta', 'geo_llms_post_meta_nonce');

        $summary = (string) get_post_meta($post->ID, self::META_SUMMARY_KEY, true);
        $pin = (string) get_post_meta($post->ID, self::META_PIN_KEY, true);
        $exclude = (string) get_post_meta($post->ID, self::META_EXCLUDE_KEY, true);
        ?>
        <p>
            <label for="geo-llms-summary"><strong>Custom llms 摘要</strong></label>
            <textarea id="geo-llms-summary" name="geo_llms_summary" style="min-height:90px;width:100%;"><?php echo esc_textarea($summary); ?></textarea>
        </p>
        <p>
            <label>
                <input type="checkbox" name="geo_llms_pin" value="1" <?php checked($pin, '1'); ?>>
                Pin 到 llms
            </label>
        </p>
        <p>
            <label>
                <input type="checkbox" name="geo_llms_exclude" value="1" <?php checked($exclude, '1'); ?>>
                从 llms 排除
            </label>
        </p>
        <p class="description">摘要会覆盖自动提取内容。Pin 会优先进入 `Featured Content`。</p>
        <?php
    }

    public static function save_post_llms_meta($post_id, $post) {
        if (!$post instanceof WP_Post) {
            return;
        }

        if (!isset($_POST['geo_llms_post_meta_nonce']) || !wp_verify_nonce(wp_unslash($_POST['geo_llms_post_meta_nonce']), 'geo_llms_post_meta')) {
            return;
        }

        if (wp_is_post_autosave($post_id) || wp_is_post_revision($post_id)) {
            return;
        }

        if (!current_user_can('edit_post', $post_id)) {
            return;
        }

        $summary = isset($_POST['geo_llms_summary']) ? sanitize_textarea_field(wp_unslash($_POST['geo_llms_summary'])) : '';
        $pin = !empty($_POST['geo_llms_pin']) ? '1' : '';
        $exclude = !empty($_POST['geo_llms_exclude']) ? '1' : '';

        if ($summary !== '') {
            update_post_meta($post_id, self::META_SUMMARY_KEY, $summary);
        } else {
            delete_post_meta($post_id, self::META_SUMMARY_KEY);
        }

        if ($pin === '1') {
            update_post_meta($post_id, self::META_PIN_KEY, '1');
        } else {
            delete_post_meta($post_id, self::META_PIN_KEY);
        }

        if ($exclude === '1') {
            update_post_meta($post_id, self::META_EXCLUDE_KEY, '1');
        } else {
            delete_post_meta($post_id, self::META_EXCLUDE_KEY);
        }

        if ($post->post_status === 'publish') {
            self::schedule_regeneration(3);
        }
    }

    public static function register_admin_page() {
        $capability = self::get_management_capability();
        if ($capability === 'manage_options') {
            add_options_page(
                'GEO LLMS Auto',
                'GEO LLMS Auto',
                $capability,
                self::ADMIN_SLUG,
                array(__CLASS__, 'render_admin_page')
            );
            return;
        }

        add_menu_page(
            'GEO LLMS Auto',
            'GEO LLMS Auto',
            $capability,
            self::ADMIN_SLUG,
            array(__CLASS__, 'render_admin_page'),
            'dashicons-chart-line',
            81
        );
    }

    public static function render_admin_page() {
        if (!current_user_can(self::get_management_capability())) {
            return;
        }

        $state = get_option(self::OPTION_KEY, array());
        $scan = get_option(self::SCAN_OPTION_KEY, array());
        $settings = self::get_settings();
        $notice = self::consume_notice();
        $preview = self::get_fix_preview();
        $history = self::get_scan_history();
        $backup = self::get_settings_backup();
        $logs = self::get_logs();
        $next_scan_ts = wp_next_scheduled(self::SCAN_CRON_HOOK);
        $integration = self::get_integration_context();
        $available_post_types = self::get_available_llms_post_types();
        $available_terms = self::get_available_filter_terms(self::get_included_post_types($settings));
        $selected_term_keys = self::get_selected_term_keys($settings);
        ?>
        <div class="wrap">
            <h1>GEO LLMS Auto Regenerator</h1>
            <style>
                .geo-card { background: #fff; border: 1px solid #dcdcde; border-radius: 8px; margin: 16px 0; padding: 18px 20px; }
                .geo-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
                .geo-metric { background: #f6f7f7; border-radius: 8px; padding: 14px 16px; }
                .geo-status { border-radius: 999px; display: inline-block; font-size: 12px; font-weight: 600; line-height: 1; padding: 7px 10px; text-transform: uppercase; }
                .geo-status-pass { background: #e8f7ec; color: #0a6b2d; }
                .geo-status-warn { background: #fff4d6; color: #8a5a00; }
                .geo-status-fail { background: #fde8e8; color: #b42318; }
                .geo-status-info { background: #e9f2ff; color: #175cd3; }
                .geo-table { border-collapse: collapse; width: 100%; }
                .geo-table td, .geo-table th { border-top: 1px solid #f0f0f1; padding: 12px 10px; text-align: left; vertical-align: top; }
                .geo-table th { border-top: 0; font-size: 13px; padding-top: 0; }
                .geo-muted { color: #50575e; }
                .geo-list { margin: 6px 0 0 18px; }
                .geo-actions form { display: inline-block; margin-right: 10px; margin-bottom: 10px; }
                .geo-help { color: #646970; font-size: 13px; }
                .geo-code { background: #f6f7f7; border-radius: 4px; padding: 2px 6px; }
                .geo-checkbox { display: block; margin-bottom: 8px; }
                .geo-seo-plugin { margin-top: 10px; }
                .geo-textarea { min-height: 110px; width: 100%; }
            </style>

            <?php if (!empty($notice['message'])) : ?>
                <div class="notice notice-<?php echo esc_attr(!empty($notice['type']) ? $notice['type'] : 'info'); ?> is-dismissible">
                    <p><?php echo esc_html($notice['message']); ?></p>
                </div>
            <?php endif; ?>

            <div class="geo-card">
                <p>插件会自动重建站点根目录的 <code>llms.txt</code> 与 <code>llms-full.txt</code>，并提供 GEO 扫描与安全修复。</p>
                <div class="geo-grid">
                    <div class="geo-metric">
                        <strong>最近一次 llms 重建</strong>
                        <p><?php echo isset($state['time']) ? esc_html($state['time']) : '尚未执行'; ?></p>
                        <p class="geo-muted">状态：<?php echo !empty($state['ok']) ? '成功' : '未记录'; ?></p>
                        <?php if (!empty($state['cache_purge']['status'])) : ?>
                            <p class="geo-muted">缓存联动：<?php echo esc_html(strtoupper($state['cache_purge']['status'])); ?></p>
                        <?php endif; ?>
                    </div>
                    <div class="geo-metric">
                        <strong>最近一次 GEO 扫描</strong>
                        <p><?php echo isset($scan['time']) ? esc_html($scan['time']) : '尚未执行'; ?></p>
                        <p class="geo-muted">
                            <?php
                            if (!empty($scan['summary'])) {
                                echo esc_html('通过 ' . (int) $scan['summary']['pass'] . ' / 警告 ' . (int) $scan['summary']['warn'] . ' / 失败 ' . (int) $scan['summary']['fail']);
                            } else {
                                echo '暂无结果';
                            }
                            ?>
                        </p>
                        <?php if (!empty($scan['auto_fix']['enabled'])) : ?>
                            <p class="geo-muted">
                                自动修复：<?php echo !empty($scan['auto_fix']['applied']) ? '已执行' : '已启用（本次无可修复项）'; ?>
                            </p>
                        <?php endif; ?>
                    </div>
                    <div class="geo-metric">
                        <strong>当前网站地址</strong>
                        <p><?php echo esc_html(home_url('/')); ?></p>
                        <p class="geo-muted">WordPress 站点根目录写权限必须可用。</p>
                    </div>
                </div>
                <div class="geo-actions">
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_regenerate_now'); ?>
                        <input type="hidden" name="action" value="geo_llms_regenerate_now">
                        <?php submit_button('立即重建 llms 文件', 'primary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_run_scan'); ?>
                        <input type="hidden" name="action" value="geo_llms_run_scan">
                        <?php submit_button('立即扫描 GEO', 'secondary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_preview_safe_fixes'); ?>
                        <input type="hidden" name="action" value="geo_llms_preview_safe_fixes">
                        <?php submit_button('预览安全修复', 'secondary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_apply_safe_fixes'); ?>
                        <input type="hidden" name="action" value="geo_llms_apply_safe_fixes">
                        <?php submit_button('一键应用安全修复', 'secondary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_rollback_safe_fixes'); ?>
                        <input type="hidden" name="action" value="geo_llms_rollback_safe_fixes">
                        <?php submit_button('回滚上次修复', 'secondary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_reset_defaults'); ?>
                        <input type="hidden" name="action" value="geo_llms_reset_defaults">
                        <?php submit_button('恢复默认设置', 'delete', 'submit', false); ?>
                    </form>
                </div>
                <p class="geo-help">“安全修复”只会启用确定性强的能力：缺失 llms 文件自动重建、首页声明 llms link、低价值页 noindex、WP 层端点修复，以及在未检测到常见 SEO 插件时补基础 OG/Twitter 与 schema。</p>
                <?php if (!empty($backup['time'])) : ?>
                    <p class="geo-help">最近一次可回滚快照：<?php echo esc_html($backup['time']); ?></p>
                <?php endif; ?>
            </div>

            <?php if (!empty($preview)) : ?>
                <div class="geo-card">
                    <h2>安全修复预览</h2>
                    <p><?php echo esc_html(isset($preview['summary']) ? $preview['summary'] : ''); ?></p>
                    <?php if (!empty($preview['changes'])) : ?>
                        <ul class="geo-list">
                            <?php foreach ($preview['changes'] as $change) : ?>
                                <li>
                                    <?php
                                    echo esc_html(
                                        $change['label']
                                        . '：'
                                        . ($change['from'] ? '开启' : '关闭')
                                        . ' -> '
                                        . ($change['to'] ? '开启' : '关闭')
                                        . '；'
                                        . $change['reason']
                                    );
                                    ?>
                                </li>
                            <?php endforeach; ?>
                        </ul>
                    <?php else : ?>
                        <p class="geo-help">当前配置已经接近预设安全修复目标。</p>
                    <?php endif; ?>
                    <?php if (!empty($preview['skipped'])) : ?>
                        <ul class="geo-list">
                            <?php foreach ($preview['skipped'] as $skipped) : ?>
                                <li><?php echo esc_html($skipped); ?></li>
                            <?php endforeach; ?>
                        </ul>
                    <?php endif; ?>
                </div>
            <?php endif; ?>

            <div class="geo-card">
                <h2>兼容环境</h2>
                <div class="geo-grid">
                    <div class="geo-metric">
                        <strong>SEO 插件</strong>
                        <p><?php echo esc_html(!empty($integration['seo_plugins']) ? implode(' / ', $integration['seo_plugins']) : '未检测到'); ?></p>
                    </div>
                    <div class="geo-metric">
                        <strong>首页模式</strong>
                        <p><?php echo esc_html($integration['front_page_mode_label']); ?></p>
                        <?php if (!empty($integration['front_page_title'])) : ?>
                            <p class="geo-muted">Front Page: <?php echo esc_html($integration['front_page_title']); ?></p>
                        <?php endif; ?>
                        <?php if (!empty($integration['posts_page_title'])) : ?>
                            <p class="geo-muted">Posts Page: <?php echo esc_html($integration['posts_page_title']); ?></p>
                        <?php endif; ?>
                    </div>
                    <div class="geo-metric">
                        <strong>WooCommerce</strong>
                        <p><?php echo !empty($integration['woocommerce']) ? '已启用' : '未启用'; ?></p>
                    </div>
                    <div class="geo-metric">
                        <strong>多语言</strong>
                        <p><?php echo esc_html(!empty($integration['multilingual']) ? implode(' / ', $integration['multilingual']) : '未检测到'); ?></p>
                    </div>
                </div>
            </div>

            <div class="geo-card">
                <h2>规则与修复设置</h2>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                    <?php wp_nonce_field('geo_llms_save_settings'); ?>
                    <input type="hidden" name="action" value="geo_llms_save_settings">
                    <h3>LLMS 规则中心</h3>
                    <p class="geo-help">这里决定哪些内容会进入 `llms.txt` / `llms-full.txt`。单篇文章的摘要、Pin、排除在编辑页侧边栏里设置。</p>
                    <p><strong>纳入的内容类型</strong></p>
                    <?php foreach ($available_post_types as $post_type => $obj) : ?>
                        <label class="geo-checkbox">
                            <input type="checkbox" name="settings[included_post_types][]" value="<?php echo esc_attr($post_type); ?>" <?php checked(in_array($post_type, self::get_included_post_types($settings), true)); ?>>
                            <?php echo esc_html(!empty($obj->labels->singular_name) ? $obj->labels->singular_name : $post_type); ?>
                            <span class="geo-muted">(<?php echo esc_html($post_type); ?>)</span>
                        </label>
                    <?php endforeach; ?>

                    <p><strong>纳入的分类 / 类目</strong></p>
                    <p class="geo-help">不选表示全部纳入。这里会自动识别 `category`、`product_cat` 这类层级 taxonomy。</p>
                    <?php if (!empty($available_terms)) : ?>
                        <?php foreach ($available_terms as $taxonomy => $group) : ?>
                            <p><strong><?php echo esc_html($group['label']); ?></strong></p>
                            <?php foreach ($group['terms'] as $term) : ?>
                                <?php $term_key = $taxonomy . ':' . (int) $term->term_id; ?>
                                <label class="geo-checkbox">
                                    <input type="checkbox" name="settings[included_term_keys][]" value="<?php echo esc_attr($term_key); ?>" <?php checked(in_array($term_key, $selected_term_keys, true)); ?>>
                                    <?php echo esc_html($term->name); ?>
                                    <span class="geo-muted">(<?php echo (int) $term->count; ?>)</span>
                                </label>
                            <?php endforeach; ?>
                        <?php endforeach; ?>
                    <?php else : ?>
                        <p class="geo-help">当前选中的内容类型没有可过滤的层级分类。</p>
                    <?php endif; ?>

                    <p>
                        <label for="geo-pinned-refs"><strong>手动 Pin 内容</strong></label><br>
                        <textarea id="geo-pinned-refs" class="geo-textarea code" name="settings[pinned_refs]" placeholder="123&#10;/about/&#10;best-article-slug"><?php echo esc_textarea(isset($settings['pinned_refs']) ? $settings['pinned_refs'] : ''); ?></textarea>
                    </p>
                    <p class="geo-help">每行一个，支持文章 ID、URL、路径或 slug。优先进入 `Featured Content`。</p>

                    <p>
                        <label for="geo-excluded-refs"><strong>全局排除规则</strong></label><br>
                        <textarea id="geo-excluded-refs" class="geo-textarea code" name="settings[excluded_refs]" placeholder="/sample-page/&#10;hello-world&#10;456"><?php echo esc_textarea(isset($settings['excluded_refs']) ? $settings['excluded_refs'] : ''); ?></textarea>
                    </p>
                    <p class="geo-help">每行一个，支持文章 ID、URL、路径或 slug。命中后会从 llms 里移除。</p>

                    <h3>安全修复</h3>
                    <p>
                        <label for="geo-safe-fix-mode"><strong>安全修复模式</strong></label><br>
                        <select id="geo-safe-fix-mode" name="settings[safe_fix_mode]">
                            <option value="strict" <?php selected(isset($settings['safe_fix_mode']) ? $settings['safe_fix_mode'] : 'strict', 'strict'); ?>>Strict（仅低风险修复）</option>
                            <option value="balanced" <?php selected(isset($settings['safe_fix_mode']) ? $settings['safe_fix_mode'] : 'strict', 'balanced'); ?>>Balanced（额外补 OG/Schema）</option>
                        </select>
                    </p>
                    <p class="geo-help">Strict 不会自动修复 H1/H2 等结构标签，也不会改 CSS/UI。Balanced 仅额外启用 head 层 OG/Schema 补全，不改模板结构。</p>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[exclude_low_value_from_llms]" value="1" <?php checked(!empty($settings['exclude_low_value_from_llms'])); ?>>
                        生成 llms 时排除低价值页、空标题页、示例页
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[enable_low_value_noindex]" value="1" <?php checked(!empty($settings['enable_low_value_noindex'])); ?>>
                        为低价值页添加 noindex（登录、找回密码、搜索页、购物车/结账/账户页、示例页）
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[enable_llms_link_tag]" value="1" <?php checked(!empty($settings['enable_llms_link_tag'])); ?>>
                        在首页 <head> 输出 <code>&lt;link rel="llms" href="/llms.txt"&gt;</code>
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[enable_wp_endpoint_fix]" value="1" <?php checked(!empty($settings['enable_wp_endpoint_fix'])); ?>>
                        启用 WP 层端点修复（robots.txt / sitemap.xml / sitemap_index.xml / wp-sitemap.xml）
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[enable_fallback_social_meta]" value="1" <?php checked(!empty($settings['enable_fallback_social_meta'])); ?>>
                        补基础 OG / Twitter 标签（仅在未检测到常见 SEO 插件时输出）
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[enable_fallback_schema_markup]" value="1" <?php checked(!empty($settings['enable_fallback_schema_markup'])); ?>>
                        补基础 schema（首页 Organization/WebSite，文章页 Article；仅在未检测到常见 SEO 插件时输出）
                    </label>
                    <p>
                        <label for="geo-org-logo"><strong>Organization Logo URL</strong></label><br>
                        <input id="geo-org-logo" class="regular-text code" type="url" name="settings[organization_logo_url]" value="<?php echo esc_attr($settings['organization_logo_url']); ?>">
                    </p>
                    <p>
                        <label for="geo-org-sameas"><strong>Organization sameAs</strong></label><br>
                        <textarea id="geo-org-sameas" class="geo-textarea code" name="settings[organization_sameas]" placeholder="https://x.com/your-account&#10;https://www.youtube.com/@your-channel"><?php echo esc_textarea($settings['organization_sameas']); ?></textarea>
                    </p>
                    <p class="geo-help">`sameAs` 每行一个 URL。这里不做自动猜测，避免写错品牌资料。</p>
                    <p class="geo-help geo-seo-plugin">
                        当前兼容识别：
                        <span class="geo-code"><?php echo esc_html(!empty($integration['seo_plugins']) ? implode(' / ', $integration['seo_plugins']) : '未检测到 SEO 插件'); ?></span>
                    </p>

                    <h3>缓存联动</h3>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[cache_purge_enabled]" value="1" <?php checked(!empty($settings['cache_purge_enabled'])); ?>>
                        启用缓存联动（重建 llms 后自动清理缓存）
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[cache_purge_local_enabled]" value="1" <?php checked(!empty($settings['cache_purge_local_enabled'])); ?>>
                        自动调用常见 WordPress 页面缓存接口
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[cache_purge_cloudflare_enabled]" value="1" <?php checked(!empty($settings['cache_purge_cloudflare_enabled'])); ?>>
                        自动清理 Cloudflare 缓存
                    </label>
                    <p>
                        <label for="geo-cache-cf-zone"><strong>Cloudflare Zone ID</strong></label><br>
                        <input id="geo-cache-cf-zone" class="regular-text code" type="text" name="settings[cache_purge_cloudflare_zone_id]" value="<?php echo esc_attr(isset($settings['cache_purge_cloudflare_zone_id']) ? $settings['cache_purge_cloudflare_zone_id'] : ''); ?>">
                    </p>
                    <p>
                        <label for="geo-cache-cf-token"><strong>Cloudflare API Token</strong></label><br>
                        <input id="geo-cache-cf-token" class="regular-text code" type="password" autocomplete="new-password" name="settings[cache_purge_cloudflare_api_token]" value="<?php echo esc_attr(isset($settings['cache_purge_cloudflare_api_token']) ? $settings['cache_purge_cloudflare_api_token'] : ''); ?>">
                    </p>
                    <p>
                        <label for="geo-cache-cf-mode"><strong>Cloudflare 清理模式</strong></label><br>
                        <select id="geo-cache-cf-mode" name="settings[cache_purge_cloudflare_mode]">
                            <option value="selected" <?php selected(isset($settings['cache_purge_cloudflare_mode']) ? $settings['cache_purge_cloudflare_mode'] : '', 'selected'); ?>>按 URL 精准清理</option>
                            <option value="everything" <?php selected(isset($settings['cache_purge_cloudflare_mode']) ? $settings['cache_purge_cloudflare_mode'] : '', 'everything'); ?>>Purge Everything</option>
                        </select>
                    </p>
                    <p>
                        <label for="geo-cache-extra-urls"><strong>额外清理 URL</strong></label><br>
                        <textarea id="geo-cache-extra-urls" class="geo-textarea code" name="settings[cache_purge_additional_urls]" placeholder="/category/ai/&#10;https://example.com/special-page/"><?php echo esc_textarea(isset($settings['cache_purge_additional_urls']) ? $settings['cache_purge_additional_urls'] : ''); ?></textarea>
                    </p>
                    <p class="geo-help">默认会清理首页、llms、robots、sitemap，以及本次内容更新涉及的文章 / 作者 / 分类 URL。这里可额外补你自己的栏目页或聚合页。</p>

                    <h3>定时扫描与历史</h3>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[scheduled_scan_enabled]" value="1" <?php checked(!empty($settings['scheduled_scan_enabled'])); ?>>
                        启用定时扫描
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[auto_safe_fix_enabled]" value="1" <?php checked(!empty($settings['auto_safe_fix_enabled'])); ?>>
                        扫描后自动执行安全修复（仅修复确定项）
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[auto_safe_fix_on_manual_scan]" value="1" <?php checked(!empty($settings['auto_safe_fix_on_manual_scan'])); ?>>
                        手动扫描时也自动执行安全修复
                    </label>
                    <p class="geo-help">当前自动修复仅覆盖 4 个安全项：llms 缺失重建、首页 LLMS Link、低价值页 noindex、WP 端点修复。</p>
                    <p>
                        <label for="geo-scan-frequency"><strong>扫描频率</strong></label><br>
                        <select id="geo-scan-frequency" name="settings[scheduled_scan_frequency]">
                            <option value="daily" <?php selected(isset($settings['scheduled_scan_frequency']) ? $settings['scheduled_scan_frequency'] : '', 'daily'); ?>>每天</option>
                            <option value="weekly" <?php selected(isset($settings['scheduled_scan_frequency']) ? $settings['scheduled_scan_frequency'] : '', 'weekly'); ?>>每周</option>
                        </select>
                    </p>
                    <p>
                        <label for="geo-scan-weekday"><strong>每周扫描日</strong></label><br>
                        <select id="geo-scan-weekday" name="settings[scheduled_scan_weekday]">
                            <?php foreach (array('mon' => '周一', 'tue' => '周二', 'wed' => '周三', 'thu' => '周四', 'fri' => '周五', 'sat' => '周六', 'sun' => '周日') as $weekday_key => $weekday_label) : ?>
                                <option value="<?php echo esc_attr($weekday_key); ?>" <?php selected(isset($settings['scheduled_scan_weekday']) ? $settings['scheduled_scan_weekday'] : '', $weekday_key); ?>><?php echo esc_html($weekday_label); ?></option>
                            <?php endforeach; ?>
                        </select>
                    </p>
                    <p>
                        <label for="geo-scan-hour"><strong>扫描时间</strong></label><br>
                        <select id="geo-scan-hour" name="settings[scheduled_scan_hour]">
                            <?php for ($hour = 0; $hour <= 23; $hour++) : ?>
                                <option value="<?php echo esc_attr($hour); ?>" <?php selected((int) (isset($settings['scheduled_scan_hour']) ? $settings['scheduled_scan_hour'] : 9), $hour); ?>><?php echo esc_html(sprintf('%02d:00', $hour)); ?></option>
                            <?php endfor; ?>
                        </select>
                    </p>
                    <p>
                        <label for="geo-history-limit"><strong>保留历史条数</strong></label><br>
                        <input id="geo-history-limit" type="number" min="5" max="100" name="settings[scan_history_limit]" value="<?php echo esc_attr(isset($settings['scan_history_limit']) ? $settings['scan_history_limit'] : 20); ?>">
                    </p>
                    <p class="geo-help">
                        下次计划扫描：
                        <span class="geo-code"><?php echo $next_scan_ts ? esc_html(wp_date('Y-m-d H:i:s', $next_scan_ts)) : '未启用'; ?></span>
                    </p>

                    <h3>通知能力</h3>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[notify_on_fail]" value="1" <?php checked(!empty($settings['notify_on_fail'])); ?>>
                        Fail 时发送通知
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[notify_on_warn]" value="1" <?php checked(!empty($settings['notify_on_warn'])); ?>>
                        仅 Warn 时也发送通知
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[notify_on_manual_scan]" value="1" <?php checked(!empty($settings['notify_on_manual_scan'])); ?>>
                        手动扫描时也发送通知
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[notify_email_enabled]" value="1" <?php checked(!empty($settings['notify_email_enabled'])); ?>>
                        启用邮件通知
                    </label>
                    <p>
                        <label for="geo-notification-email"><strong>通知邮箱</strong></label><br>
                        <input id="geo-notification-email" class="regular-text code" type="email" name="settings[notification_email]" value="<?php echo esc_attr(isset($settings['notification_email']) ? $settings['notification_email'] : ''); ?>">
                    </p>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[notify_webhook_enabled]" value="1" <?php checked(!empty($settings['notify_webhook_enabled'])); ?>>
                        启用 Webhook 通知
                    </label>
                    <p>
                        <label for="geo-webhook-url"><strong>Webhook URL</strong></label><br>
                        <input id="geo-webhook-url" class="regular-text code" type="url" name="settings[notification_webhook_url]" value="<?php echo esc_attr(isset($settings['notification_webhook_url']) ? $settings['notification_webhook_url'] : ''); ?>">
                    </p>
                    <p>
                        <label for="geo-email-subject-template"><strong>邮件标题模板</strong></label><br>
                        <input id="geo-email-subject-template" class="large-text code" type="text" name="settings[notification_email_subject_template]" value="<?php echo esc_attr(isset($settings['notification_email_subject_template']) ? $settings['notification_email_subject_template'] : ''); ?>">
                    </p>
                    <p>
                        <label for="geo-email-body-template"><strong>邮件正文模板</strong></label><br>
                        <textarea id="geo-email-body-template" class="geo-textarea code" name="settings[notification_email_body_template]"><?php echo esc_textarea(isset($settings['notification_email_body_template']) ? $settings['notification_email_body_template'] : ''); ?></textarea>
                    </p>
                    <p>
                        <label for="geo-webhook-template"><strong>Webhook 模板</strong></label><br>
                        <textarea id="geo-webhook-template" class="geo-textarea code" name="settings[notification_webhook_template]"><?php echo esc_textarea(isset($settings['notification_webhook_template']) ? $settings['notification_webhook_template'] : ''); ?></textarea>
                    </p>
                    <p class="geo-help">可用占位符：`{{site_name}}`、`{{site_url}}`、`{{scan_time}}`、`{{trigger}}`、`{{overall_status}}`、`{{summary}}`、`{{trend}}`、`{{issues}}`、`{{recommendations}}`。Webhook 默认模板额外提供 `*_json` 版本占位符。</p>

                    <h3>产品基础设施</h3>
                    <p>
                        <label for="geo-capability"><strong>后台权限</strong></label><br>
                        <select id="geo-capability" name="settings[management_capability]">
                            <option value="manage_options" <?php selected(isset($settings['management_capability']) ? $settings['management_capability'] : '', 'manage_options'); ?>>管理员（manage_options）</option>
                            <option value="edit_pages" <?php selected(isset($settings['management_capability']) ? $settings['management_capability'] : '', 'edit_pages'); ?>>编辑（edit_pages）</option>
                            <option value="publish_posts" <?php selected(isset($settings['management_capability']) ? $settings['management_capability'] : '', 'publish_posts'); ?>>作者/编辑（publish_posts）</option>
                        </select>
                    </p>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[logging_enabled]" value="1" <?php checked(!empty($settings['logging_enabled'])); ?>>
                        启用错误日志
                    </label>
                    <label class="geo-checkbox">
                        <input type="checkbox" name="settings[cleanup_on_uninstall]" value="1" <?php checked(!empty($settings['cleanup_on_uninstall'])); ?>>
                        卸载插件时清理设置、历史和日志
                    </label>
                    <p class="geo-help">插件已启用 textdomain 加载，后续翻译文件可放在 `languages/`。当前界面语言跟随 WordPress 站点语言。</p>
                    <?php submit_button('保存设置'); ?>
                </form>
            </div>

            <?php if (!empty($scan['endpoint_checks'])) : ?>
                <div class="geo-card">
                    <h2>GEO 端点扫描</h2>
                    <?php self::render_checks_table($scan['endpoint_checks'], true); ?>
                </div>
            <?php endif; ?>

            <?php if (!empty($scan['signal_checks'])) : ?>
                <div class="geo-card">
                    <h2>SEO / GEO 信号扫描</h2>
                    <?php self::render_checks_table($scan['signal_checks'], false); ?>
                </div>
            <?php endif; ?>

            <?php if (!empty($scan['recommendations'])) : ?>
                <div class="geo-card">
                    <h2>建议动作</h2>
                    <ul class="geo-list">
                        <?php foreach ($scan['recommendations'] as $recommendation) : ?>
                            <li><?php echo esc_html($recommendation); ?></li>
                        <?php endforeach; ?>
                    </ul>
                </div>
            <?php endif; ?>

            <?php if (!empty($scan['notifications'])) : ?>
                <div class="geo-card">
                    <h2>最近一次通知结果</h2>
                    <ul class="geo-list">
                        <?php foreach ($scan['notifications'] as $channel => $result) : ?>
                            <li><?php echo esc_html(strtoupper($channel) . ': ' . $result); ?></li>
                        <?php endforeach; ?>
                    </ul>
                </div>
            <?php endif; ?>

            <?php if (!empty($state['cache_purge'])) : ?>
                <div class="geo-card">
                    <h2>最近一次缓存联动</h2>
                    <p>
                        <?php self::render_status_badge(isset($state['cache_purge']['status']) ? $state['cache_purge']['status'] : 'info'); ?>
                        <span class="geo-muted"><?php echo esc_html(isset($state['cache_purge']['time']) ? $state['cache_purge']['time'] : ''); ?></span>
                    </p>
                    <?php if (!empty($state['cache_purge']['details'])) : ?>
                        <ul class="geo-list">
                            <?php foreach ((array) $state['cache_purge']['details'] as $detail) : ?>
                                <li><?php echo esc_html($detail); ?></li>
                            <?php endforeach; ?>
                        </ul>
                    <?php endif; ?>
                    <?php if (!empty($state['cache_purge']['urls'])) : ?>
                        <p class="geo-help">本次涉及 URL 数量：<?php echo esc_html((string) count((array) $state['cache_purge']['urls'])); ?></p>
                    <?php endif; ?>
                </div>
            <?php endif; ?>

            <div class="geo-card">
                <h2>报告导出</h2>
                <p class="geo-help">导出最新一次扫描结果，适合发给运营、开发或客户。若当前还没有扫描结果，插件会先临时生成一份报告。</p>
                <div class="geo-actions">
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_export_report'); ?>
                        <input type="hidden" name="action" value="geo_llms_export_report">
                        <input type="hidden" name="format" value="markdown">
                        <?php submit_button('导出 Markdown', 'secondary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_export_report'); ?>
                        <input type="hidden" name="action" value="geo_llms_export_report">
                        <input type="hidden" name="format" value="json">
                        <?php submit_button('导出 JSON', 'secondary', 'submit', false); ?>
                    </form>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_export_report'); ?>
                        <input type="hidden" name="action" value="geo_llms_export_report">
                        <input type="hidden" name="format" value="csv">
                        <?php submit_button('导出 CSV', 'secondary', 'submit', false); ?>
                    </form>
                </div>
            </div>

            <div class="geo-card">
                <h2>配置导入 / 导出</h2>
                <div class="geo-grid">
                    <div class="geo-metric">
                        <strong>导出当前配置</strong>
                        <p class="geo-help">导出完整 JSON，适合备份或迁移到其他 WordPress 站点。</p>
                        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                            <?php wp_nonce_field('geo_llms_export_settings'); ?>
                            <input type="hidden" name="action" value="geo_llms_export_settings">
                            <?php submit_button('导出配置 JSON', 'secondary', 'submit', false); ?>
                        </form>
                    </div>
                    <div class="geo-metric">
                        <strong>导入配置</strong>
                        <p class="geo-help">支持上传之前导出的 JSON 文件，也支持直接粘贴 JSON。</p>
                        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" enctype="multipart/form-data">
                            <?php wp_nonce_field('geo_llms_import_settings'); ?>
                            <input type="hidden" name="action" value="geo_llms_import_settings">
                            <p><input type="file" name="settings_import_file" accept=".json,application/json"></p>
                            <p><textarea class="geo-textarea code" name="settings_import_json" placeholder="{&quot;settings&quot;:{...}}"></textarea></p>
                            <?php submit_button('导入配置', 'secondary', 'submit', false); ?>
                        </form>
                    </div>
                </div>
            </div>

            <div class="geo-card">
                <h2>扫描历史趋势</h2>
                <?php if (!empty($history)) : ?>
                    <table class="geo-table">
                        <thead>
                            <tr>
                                <th>时间</th>
                                <th>触发方式</th>
                                <th>状态</th>
                                <th>摘要</th>
                                <th>趋势</th>
                            </tr>
                        </thead>
                        <tbody>
                            <?php foreach (array_slice($history, 0, 10) as $entry) : ?>
                                <tr>
                                    <td><?php echo esc_html(isset($entry['time']) ? $entry['time'] : ''); ?></td>
                                    <td><?php echo esc_html(isset($entry['trigger']) ? $entry['trigger'] : 'manual'); ?></td>
                                    <td><?php self::render_status_badge(isset($entry['overall_status']) ? $entry['overall_status'] : 'info'); ?></td>
                                    <td>
                                        <?php
                                        $entry_summary = isset($entry['summary']) && is_array($entry['summary']) ? $entry['summary'] : array();
                                        echo esc_html(
                                            'Pass ' . (int) (isset($entry_summary['pass']) ? $entry_summary['pass'] : 0)
                                            . ' / Warn ' . (int) (isset($entry_summary['warn']) ? $entry_summary['warn'] : 0)
                                            . ' / Fail ' . (int) (isset($entry_summary['fail']) ? $entry_summary['fail'] : 0)
                                        );
                                        ?>
                                    </td>
                                    <td>
                                        <?php echo esc_html(!empty($entry['trend']['summary']) ? $entry['trend']['summary'] : 'No previous scan'); ?>
                                    </td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php else : ?>
                    <p class="geo-help">还没有历史记录。执行一次扫描或启用定时扫描后，这里会开始积累趋势数据。</p>
                <?php endif; ?>
            </div>

            <div class="geo-card">
                <h2>错误日志</h2>
                <p class="geo-help">记录升级、缓存联动、导入导出、通知失败等关键事件。默认保留最近 100 条。</p>
                <div class="geo-actions">
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                        <?php wp_nonce_field('geo_llms_clear_logs'); ?>
                        <input type="hidden" name="action" value="geo_llms_clear_logs">
                        <?php submit_button('清空日志', 'secondary', 'submit', false); ?>
                    </form>
                </div>
                <?php if (!empty($logs)) : ?>
                    <table class="geo-table">
                        <thead>
                            <tr>
                                <th>时间</th>
                                <th>级别</th>
                                <th>事件</th>
                                <th>上下文</th>
                            </tr>
                        </thead>
                        <tbody>
                            <?php foreach (array_slice($logs, 0, 20) as $log) : ?>
                                <tr>
                                    <td><?php echo esc_html(isset($log['time']) ? $log['time'] : ''); ?></td>
                                    <td><?php echo esc_html(isset($log['level']) ? strtoupper($log['level']) : 'INFO'); ?></td>
                                    <td><?php echo esc_html(isset($log['message']) ? $log['message'] : ''); ?></td>
                                    <td><code><?php echo esc_html(isset($log['context']) ? $log['context'] : ''); ?></code></td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php else : ?>
                    <p class="geo-help">当前没有日志。</p>
                <?php endif; ?>
            </div>
        </div>
        <?php
    }

    private static function render_checks_table(array $checks, $show_url) {
        ?>
        <table class="geo-table">
            <thead>
                <tr>
                    <th>检查项</th>
                    <?php if ($show_url) : ?>
                        <th>URL</th>
                    <?php endif; ?>
                    <th>状态</th>
                    <th>结果</th>
                    <th>建议</th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($checks as $check) : ?>
                    <tr>
                        <td><strong><?php echo esc_html($check['label']); ?></strong></td>
                        <?php if ($show_url) : ?>
                            <td><a href="<?php echo esc_url($check['url']); ?>" target="_blank" rel="noreferrer"><?php echo esc_html($check['url']); ?></a></td>
                        <?php endif; ?>
                        <td><?php self::render_status_badge($check['status']); ?></td>
                        <td>
                            <div><?php echo esc_html($check['summary']); ?></div>
                            <?php if (!empty($check['details'])) : ?>
                                <ul class="geo-list">
                                    <?php foreach ($check['details'] as $detail) : ?>
                                        <li><?php echo esc_html($detail); ?></li>
                                    <?php endforeach; ?>
                                </ul>
                            <?php endif; ?>
                        </td>
                        <td><?php echo !empty($check['suggestion']) ? esc_html($check['suggestion']) : '无需操作'; ?></td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    private static function render_status_badge($status) {
        $status = in_array($status, array('pass', 'warn', 'fail', 'info'), true) ? $status : 'info';
        $label_map = array(
            'pass' => 'PASS',
            'warn' => 'WARN',
            'fail' => 'FAIL',
            'info' => 'INFO',
        );
        printf(
            '<span class="geo-status geo-status-%1$s">%2$s</span>',
            esc_attr($status),
            esc_html($label_map[$status])
        );
    }

    public static function handle_manual_regenerate() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_regenerate_now');
        self::regenerate_files();
        self::log_event('info', 'manual_regeneration_triggered');
        self::set_notice('success', 'llms.txt 与 llms-full.txt 已重建。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_run_scan() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_run_scan');
        $scan = self::run_scan(true, 'manual');
        self::log_event('info', 'manual_scan_triggered');
        $summary = isset($scan['summary']) ? $scan['summary'] : array('pass' => 0, 'warn' => 0, 'fail' => 0);
        $message = 'GEO 扫描完成。通过 ' . (int) $summary['pass'] . '，警告 ' . (int) $summary['warn'] . '，失败 ' . (int) $summary['fail'] . '。';
        if (!empty($scan['auto_fix']['applied'])) {
            $message .= ' 已自动应用安全修复。';
        }
        self::set_notice('success', $message);
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_preview_safe_fixes() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_preview_safe_fixes');
        self::save_fix_preview(self::get_safe_fix_plan());
        self::set_notice('info', '已生成安全修复预览。请先看预计变更，再决定是否应用。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_apply_safe_fixes() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_apply_safe_fixes');

        $plan = self::get_safe_fix_plan();
        self::backup_current_settings();
        $settings = isset($plan['target_settings']) && is_array($plan['target_settings']) ? $plan['target_settings'] : self::get_settings();
        self::save_settings($settings);
        self::sync_scan_schedule($settings);
        self::regenerate_files();
        self::run_scan(true, 'manual', false);
        self::clear_fix_preview();
        self::log_event('info', 'safe_fixes_applied', array('change_count' => count(isset($plan['changes']) ? $plan['changes'] : array())));

        $notes = array();
        foreach (isset($plan['changes']) ? $plan['changes'] : array() as $change) {
            $notes[] = $change['label'];
        }
        if (empty($notes)) {
            $notes[] = '没有需要应用的新变更';
        }
        foreach (isset($plan['skipped']) ? $plan['skipped'] : array() as $skipped) {
            $notes[] = $skipped;
        }

        self::set_notice('success', '安全修复已应用：' . implode('；', $notes) . '。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_rollback_safe_fixes() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_rollback_safe_fixes');
        $backup = self::get_settings_backup();
        if (empty($backup['settings']) || !is_array($backup['settings'])) {
            self::set_notice('warning', '没有可回滚的安全修复快照。');
            wp_safe_redirect(self::get_admin_page_url());
            exit;
        }

        $settings = wp_parse_args($backup['settings'], self::get_default_settings());
        self::save_settings($settings);
        self::sync_scan_schedule($settings);
        self::save_scan_history(self::get_scan_history(), isset($settings['scan_history_limit']) ? $settings['scan_history_limit'] : 20);
        self::regenerate_files();
        self::run_scan(true, 'manual', false);
        self::clear_fix_preview();
        self::log_event('info', 'safe_fixes_rolled_back', array('backup_time' => isset($backup['time']) ? $backup['time'] : ''));

        self::set_notice('success', '已回滚到上一次安全修复前的设置快照（' . esc_html($backup['time']) . '）。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_reset_defaults() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_reset_defaults');
        self::backup_current_settings();
        $settings = self::get_default_settings();
        self::save_settings($settings);
        self::sync_scan_schedule($settings);
        self::save_scan_history(self::get_scan_history(), isset($settings['scan_history_limit']) ? $settings['scan_history_limit'] : 20);
        self::regenerate_files();
        self::clear_fix_preview();
        self::log_event('info', 'settings_reset_to_default');
        self::set_notice('success', '插件设置已恢复默认值。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_save_settings() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_save_settings');

        $raw = isset($_POST['settings']) && is_array($_POST['settings']) ? wp_unslash($_POST['settings']) : array();
        $settings = self::sanitize_settings_input($raw);
        self::save_settings($settings);
        self::sync_scan_schedule($settings);
        self::save_scan_history(self::get_scan_history(), isset($settings['scan_history_limit']) ? $settings['scan_history_limit'] : 20);
        self::clear_fix_preview();
        self::log_event('info', 'settings_saved', array(
            'scheduled_scan_enabled' => !empty($settings['scheduled_scan_enabled']),
            'cache_purge_enabled' => !empty($settings['cache_purge_enabled']),
        ));
        self::set_notice('success', 'GEO 设置已保存。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_export_report() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_export_report');
        $format = isset($_POST['format']) ? sanitize_key(wp_unslash($_POST['format'])) : 'json';
        $scan = get_option(self::SCAN_OPTION_KEY, array());
        if (empty($scan)) {
            $scan = self::run_scan(false, 'export');
        }

        $report = self::build_report_payload($scan);
        $date = gmdate('Ymd-His');

        if ($format === 'markdown') {
            self::stream_download('geo-report-' . $date . '.md', 'text/markdown; charset=utf-8', self::build_markdown_report($report));
        }

        if ($format === 'csv') {
            self::stream_download('geo-report-' . $date . '.csv', 'text/csv; charset=utf-8', self::build_csv_report($report));
        }

        self::stream_download('geo-report-' . $date . '.json', 'application/json; charset=utf-8', self::build_json_report($report));
    }

    public static function handle_export_settings() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_export_settings');
        $payload = array(
            'plugin' => 'geo-llms-auto-regenerator',
            'version' => self::VERSION,
            'exported_at' => current_time('mysql'),
            'site_url' => home_url('/'),
            'settings' => self::get_settings(),
        );

        self::log_event('info', 'settings_exported');
        self::stream_download(
            'geo-llms-settings-' . gmdate('Ymd-His') . '.json',
            'application/json; charset=utf-8',
            wp_json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
        );
    }

    public static function handle_import_settings() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_import_settings');

        $raw_json = '';
        if (!empty($_FILES['settings_import_file']['tmp_name']) && is_uploaded_file($_FILES['settings_import_file']['tmp_name'])) {
            $raw_json = (string) file_get_contents($_FILES['settings_import_file']['tmp_name']);
        } elseif (!empty($_POST['settings_import_json'])) {
            $raw_json = (string) wp_unslash($_POST['settings_import_json']);
        }

        if (trim($raw_json) === '') {
            self::set_notice('warning', '没有读取到可导入的 JSON 配置。');
            wp_safe_redirect(self::get_admin_page_url());
            exit;
        }

        $decoded = json_decode($raw_json, true);
        if (json_last_error() !== JSON_ERROR_NONE || !is_array($decoded)) {
            self::log_event('error', 'settings_import_failed', array('error' => json_last_error_msg()), true);
            self::set_notice('error', '配置导入失败：JSON 格式无效。');
            wp_safe_redirect(self::get_admin_page_url());
            exit;
        }

        $raw_settings = isset($decoded['settings']) && is_array($decoded['settings']) ? $decoded['settings'] : $decoded;
        self::backup_current_settings();
        $settings = self::sanitize_settings_input($raw_settings);
        self::save_settings($settings);
        self::sync_scan_schedule($settings);
        self::save_scan_history(self::get_scan_history(), isset($settings['scan_history_limit']) ? $settings['scan_history_limit'] : 20);
        self::clear_fix_preview();
        self::regenerate_files();
        self::log_event('info', 'settings_imported', array('source_version' => isset($decoded['version']) ? $decoded['version'] : 'unknown'));
        self::set_notice('success', '配置已导入，并已同步 llms 与缓存联动设置。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function handle_clear_logs() {
        if (!current_user_can(self::get_management_capability())) {
            wp_die('Permission denied');
        }

        check_admin_referer('geo_llms_clear_logs');
        self::clear_logs();
        self::set_notice('success', '错误日志已清空。');
        wp_safe_redirect(self::get_admin_page_url());
        exit;
    }

    public static function run_scheduled_scan() {
        self::run_scan(true, 'scheduled');
    }

    private static function run_scan($persist = true, $trigger = 'manual', $allow_auto_fix = true) {
        $previous_scan = $persist ? get_option(self::SCAN_OPTION_KEY, array()) : array();
        $robots_response = self::fetch_url(home_url('/robots.txt'));
        $robots_rules = array();
        $robots_loaded = empty($robots_response['error']) && (int) $robots_response['status_code'] === 200;

        if ($robots_loaded) {
            $robots_rules = self::parse_robots_txt($robots_response['body']);
        }

        $endpoint_checks = self::build_endpoint_checks($robots_rules, $robots_loaded);
        $signal_checks = self::build_signal_checks();
        $recommendations = self::build_recommendations(array_merge($endpoint_checks, $signal_checks));

        $summary = array(
            'pass' => 0,
            'warn' => 0,
            'fail' => 0,
            'info' => 0,
        );

        foreach (array_merge($endpoint_checks, $signal_checks) as $check) {
            $status = isset($check['status']) ? $check['status'] : 'info';
            if (!isset($summary[$status])) {
                $status = 'info';
            }
            $summary[$status]++;
        }

        $scan = array(
            'time' => current_time('mysql'),
            'trigger' => $trigger,
            'endpoint_checks' => $endpoint_checks,
            'signal_checks' => $signal_checks,
            'summary' => $summary,
            'recommendations' => $recommendations,
        );

        if ($persist && $allow_auto_fix) {
            $settings = self::get_settings();
            if (self::should_run_auto_safe_fix($trigger, $settings)) {
                $auto_fix = self::apply_auto_safe_fix_from_scan($scan, $settings, $trigger);
                if (!empty($auto_fix['enabled'])) {
                    $scan['auto_fix'] = $auto_fix;
                }

                if (!empty($auto_fix['applied'])) {
                    $verified_scan = self::run_scan(false, $trigger . '-verify', false);
                    $scan = array_merge($scan, $verified_scan);
                    $scan['trigger'] = $trigger;
                    $scan['auto_fix'] = array_merge($auto_fix, array(
                        'before_summary' => $summary,
                        'after_summary' => isset($verified_scan['summary']) ? $verified_scan['summary'] : $summary,
                    ));
                }
            }
        }

        $scan['trend'] = self::build_scan_trend($scan, is_array($previous_scan) ? $previous_scan : array());

        if ($persist) {
            $scan['notifications'] = self::maybe_send_scan_notifications($scan, $trigger);
            update_option(self::SCAN_OPTION_KEY, $scan, false);
            self::append_scan_history($scan);
            self::log_event('info', 'scan_completed', array(
                'trigger' => $trigger,
                'pass' => isset($summary['pass']) ? (int) $summary['pass'] : 0,
                'warn' => isset($summary['warn']) ? (int) $summary['warn'] : 0,
                'fail' => isset($summary['fail']) ? (int) $summary['fail'] : 0,
            ));
        }

        return $scan;
    }

    private static function should_run_auto_safe_fix($trigger, array $settings) {
        if (empty($settings['auto_safe_fix_enabled'])) {
            return false;
        }

        if ($trigger === 'manual' && empty($settings['auto_safe_fix_on_manual_scan'])) {
            return false;
        }

        if (strpos((string) $trigger, 'verify') !== false) {
            return false;
        }

        return true;
    }

    private static function apply_auto_safe_fix_from_scan(array $scan, array $settings, $trigger) {
        $plan = self::build_issue_driven_fix_plan($scan, $settings);
        if (empty($plan['setting_changes']) && empty($plan['runtime_actions'])) {
            return array(
                'enabled' => true,
                'applied' => false,
                'time' => current_time('mysql'),
                'notes' => array('扫描结果未命中可自动修复项。'),
                'setting_changes' => array(),
                'runtime_actions' => array(),
            );
        }

        $did_apply = false;
        $applied_settings = array();
        $runtime_applied = array();
        $target_settings = $settings;

        foreach ($plan['setting_changes'] as $change) {
            $key = isset($change['key']) ? $change['key'] : '';
            if ($key === '') {
                continue;
            }
            $target_settings[$key] = (int) (isset($change['to']) ? $change['to'] : 0);
            $applied_settings[] = $change;
        }

        if (!empty($applied_settings)) {
            self::backup_current_settings();
            self::save_settings($target_settings);
            self::sync_scan_schedule($target_settings);
            $did_apply = true;
        }

        if (in_array('regenerate_llms_files', $plan['runtime_actions'], true)) {
            self::regenerate_files();
            $runtime_applied[] = 'regenerate_llms_files';
            $did_apply = true;
        } elseif (!empty($applied_settings)) {
            // Settings changed; rebuild llms and purge caches to reduce stale-scan drift.
            self::regenerate_files();
            $runtime_applied[] = 'regenerate_llms_files';
        }

        if ($did_apply) {
            self::clear_fix_preview();
            self::log_event('info', 'auto_safe_fixes_applied', array(
                'trigger' => $trigger,
                'setting_changes' => count($applied_settings),
                'runtime_actions' => $runtime_applied,
            ));
        }

        return array(
            'enabled' => true,
            'applied' => $did_apply,
            'time' => current_time('mysql'),
            'notes' => isset($plan['notes']) && is_array($plan['notes']) ? $plan['notes'] : array(),
            'setting_changes' => $applied_settings,
            'runtime_actions' => $runtime_applied,
        );
    }

    private static function build_issue_driven_fix_plan(array $scan, array $settings) {
        $setting_changes = array();
        $runtime_actions = array();
        $notes = array();
        $safe_fix_mode = self::sanitize_safe_fix_mode(isset($settings['safe_fix_mode']) ? $settings['safe_fix_mode'] : 'strict');

        $llms_endpoint_issue = self::scan_has_issue($scan, 'endpoint_checks', array('llms.txt', 'llms-full.txt'), array('fail'));
        if ($llms_endpoint_issue && self::llms_root_files_missing_or_empty()) {
            $runtime_actions[] = 'regenerate_llms_files';
            $notes[] = '检测到 llms 端点异常且根目录文件缺失/空文件，自动重建 llms 文件。';
        }

        $llms_link_issue = self::scan_has_issue($scan, 'signal_checks', array('首页 LLMS Link'), array('fail', 'warn'));
        if ($llms_link_issue && empty($settings['enable_llms_link_tag'])) {
            $setting_changes[] = array(
                'key' => 'enable_llms_link_tag',
                'label' => '首页 LLMS Link',
                'from' => 0,
                'to' => 1,
                'reason' => '首页缺失或异常时，自动启用 <link rel="llms"> 输出。',
            );
        }

        $low_value_noindex_issue = self::scan_has_issue($scan, 'signal_checks', array('低价值页 noindex'), array('fail', 'warn'));
        if ($low_value_noindex_issue && empty($settings['enable_low_value_noindex'])) {
            $setting_changes[] = array(
                'key' => 'enable_low_value_noindex',
                'label' => '低价值页 noindex',
                'from' => 0,
                'to' => 1,
                'reason' => '检测到低价值页未 noindex，自动启用 noindex 规则。',
            );
        }

        $endpoint_issue = self::scan_has_issue($scan, 'endpoint_checks', array('robots.txt', 'sitemap.xml', 'sitemap_index.xml', 'wp-sitemap.xml'), array('fail'));
        if ($endpoint_issue && empty($settings['enable_wp_endpoint_fix'])) {
            $setting_changes[] = array(
                'key' => 'enable_wp_endpoint_fix',
                'label' => 'WP 端点修复',
                'from' => 0,
                'to' => 1,
                'reason' => '检测到 robots/sitemap 端点异常，自动启用 WP 端点兜底修复。',
            );
        }

        if ($safe_fix_mode === 'balanced' && !self::detect_supported_seo_plugin()) {
            $social_issue = self::scan_has_issue($scan, 'signal_checks', array('首页 OG / Twitter', 'OG Image'), array('fail', 'warn'));
            if ($social_issue && empty($settings['enable_fallback_social_meta'])) {
                $setting_changes[] = array(
                    'key' => 'enable_fallback_social_meta',
                    'label' => '基础 OG / Twitter',
                    'from' => 0,
                    'to' => 1,
                    'reason' => 'Balanced 模式下检测到社交标签缺口，自动启用基础 OG/Twitter 补全。',
                );
            }

            $schema_issue = self::scan_has_issue($scan, 'signal_checks', array('文章 Article Schema', 'Breadcrumb Schema'), array('fail', 'warn'));
            if ($schema_issue && empty($settings['enable_fallback_schema_markup'])) {
                $setting_changes[] = array(
                    'key' => 'enable_fallback_schema_markup',
                    'label' => '基础 Schema',
                    'from' => 0,
                    'to' => 1,
                    'reason' => 'Balanced 模式下检测到结构化数据缺口，自动启用基础 Schema 补全。',
                );
            }
        } elseif ($safe_fix_mode !== 'balanced') {
            $notes[] = 'Strict 模式不会自动修复 OG/Twitter、Schema、H1/H2 结构等高风险项。';
        }

        return array(
            'setting_changes' => $setting_changes,
            'runtime_actions' => array_values(array_unique($runtime_actions)),
            'notes' => $notes,
        );
    }

    private static function scan_has_issue(array $scan, $section, array $labels, array $statuses) {
        if (empty($scan[$section]) || !is_array($scan[$section])) {
            return false;
        }

        foreach ($scan[$section] as $check) {
            $label = isset($check['label']) ? (string) $check['label'] : '';
            $status = isset($check['status']) ? (string) $check['status'] : 'info';
            if (in_array($label, $labels, true) && in_array($status, $statuses, true)) {
                return true;
            }
        }

        return false;
    }

    private static function llms_root_files_missing_or_empty() {
        $files = array('llms.txt', 'llms-full.txt');
        foreach ($files as $file) {
            $path = trailingslashit(ABSPATH) . $file;
            if (!is_readable($path)) {
                return true;
            }
            $size = (int) @filesize($path);
            if ($size <= 0) {
                return true;
            }
        }

        return false;
    }

    private static function build_scan_trend(array $scan, array $previous_scan) {
        if (empty($previous_scan['time'])) {
            return array(
                'summary' => 'Baseline scan',
                'fail_delta' => 0,
                'warn_delta' => 0,
                'new_failures' => self::get_check_labels_by_status($scan, 'fail'),
                'resolved_failures' => array(),
                'new_warnings' => self::get_check_labels_by_status($scan, 'warn'),
                'resolved_warnings' => array(),
            );
        }

        $current_summary = isset($scan['summary']) && is_array($scan['summary']) ? $scan['summary'] : array();
        $previous_summary = isset($previous_scan['summary']) && is_array($previous_scan['summary']) ? $previous_scan['summary'] : array();

        $current_fail = self::get_check_labels_by_status($scan, 'fail');
        $previous_fail = self::get_check_labels_by_status($previous_scan, 'fail');
        $current_warn = self::get_check_labels_by_status($scan, 'warn');
        $previous_warn = self::get_check_labels_by_status($previous_scan, 'warn');

        $fail_delta = (int) (isset($current_summary['fail']) ? $current_summary['fail'] : 0) - (int) (isset($previous_summary['fail']) ? $previous_summary['fail'] : 0);
        $warn_delta = (int) (isset($current_summary['warn']) ? $current_summary['warn'] : 0) - (int) (isset($previous_summary['warn']) ? $previous_summary['warn'] : 0);

        $parts = array();
        $parts[] = 'Fail ' . self::format_delta_number($fail_delta);
        $parts[] = 'Warn ' . self::format_delta_number($warn_delta);

        $new_failures = array_values(array_diff($current_fail, $previous_fail));
        $resolved_failures = array_values(array_diff($previous_fail, $current_fail));
        $new_warnings = array_values(array_diff($current_warn, $previous_warn));
        $resolved_warnings = array_values(array_diff($previous_warn, $current_warn));

        return array(
            'summary' => implode(' / ', $parts),
            'fail_delta' => $fail_delta,
            'warn_delta' => $warn_delta,
            'new_failures' => $new_failures,
            'resolved_failures' => $resolved_failures,
            'new_warnings' => $new_warnings,
            'resolved_warnings' => $resolved_warnings,
        );
    }

    private static function format_delta_number($value) {
        $value = (int) $value;
        if ($value > 0) {
            return '+' . $value;
        }
        return (string) $value;
    }

    private static function get_check_labels_by_status(array $scan, $status) {
        $labels = array();
        foreach (array('endpoint_checks', 'signal_checks') as $key) {
            if (empty($scan[$key]) || !is_array($scan[$key])) {
                continue;
            }

            foreach ($scan[$key] as $check) {
                if (!empty($check['status']) && $check['status'] === $status && !empty($check['label'])) {
                    $labels[] = (string) $check['label'];
                }
            }
        }

        return array_values(array_unique($labels));
    }

    private static function append_scan_history(array $scan) {
        $history = self::get_scan_history();
        array_unshift(
            $history,
            array(
                'time' => isset($scan['time']) ? $scan['time'] : current_time('mysql'),
                'trigger' => isset($scan['trigger']) ? $scan['trigger'] : 'manual',
                'summary' => isset($scan['summary']) ? $scan['summary'] : array(),
                'overall_status' => self::get_overall_scan_status(isset($scan['summary']) && is_array($scan['summary']) ? $scan['summary'] : array()),
                'trend' => isset($scan['trend']) ? $scan['trend'] : array(),
                'fail_labels' => self::get_check_labels_by_status($scan, 'fail'),
                'warn_labels' => self::get_check_labels_by_status($scan, 'warn'),
            )
        );

        self::save_scan_history($history);
    }

    private static function maybe_send_scan_notifications(array $scan, $trigger) {
        $settings = self::get_settings();
        $results = array();

        if ($trigger !== 'scheduled' && empty($settings['notify_on_manual_scan'])) {
            return $results;
        }

        $summary = isset($scan['summary']) && is_array($scan['summary']) ? $scan['summary'] : array();
        $has_fail = !empty($summary['fail']);
        $has_warn = !$has_fail && !empty($summary['warn']);

        if (($has_fail && empty($settings['notify_on_fail'])) || ($has_warn && empty($settings['notify_on_warn'])) || (!$has_fail && !$has_warn)) {
            return $results;
        }

        $context = self::build_notification_context($scan);

        if (!empty($settings['notify_email_enabled']) && !empty($settings['notification_email'])) {
            $subject = self::apply_notification_template(isset($settings['notification_email_subject_template']) ? $settings['notification_email_subject_template'] : self::get_default_email_subject_template(), $context);
            $body = self::apply_notification_template(isset($settings['notification_email_body_template']) ? $settings['notification_email_body_template'] : self::get_default_email_body_template(), $context);
            $results['email'] = wp_mail($settings['notification_email'], $subject, $body) ? 'sent' : 'failed';
            if ($results['email'] !== 'sent') {
                self::log_event('error', 'notification_email_failed', array('to' => $settings['notification_email']), true);
            }
        }

        if (!empty($settings['notify_webhook_enabled']) && !empty($settings['notification_webhook_url'])) {
            $payload = self::apply_notification_template(isset($settings['notification_webhook_template']) ? $settings['notification_webhook_template'] : self::get_default_webhook_template(), $context);
            $headers = array('Content-Type' => 'text/plain; charset=utf-8');

            if (self::looks_like_json($payload)) {
                $headers['Content-Type'] = 'application/json; charset=utf-8';
            }

            $response = wp_remote_post(
                $settings['notification_webhook_url'],
                array(
                    'timeout' => 15,
                    'headers' => $headers,
                    'body' => $payload,
                )
            );

            if (is_wp_error($response)) {
                $results['webhook'] = 'failed: ' . $response->get_error_message();
                self::log_event('error', 'notification_webhook_failed', array('error' => $response->get_error_message()), true);
            } else {
                $results['webhook'] = 'sent: HTTP ' . (int) wp_remote_retrieve_response_code($response);
            }
        }

        return $results;
    }

    private static function looks_like_json($payload) {
        $payload = trim((string) $payload);
        if ($payload === '' || ($payload[0] !== '{' && $payload[0] !== '[')) {
            return false;
        }

        json_decode($payload, true);
        return json_last_error() === JSON_ERROR_NONE;
    }

    private static function build_notification_context(array $scan) {
        $summary = isset($scan['summary']) && is_array($scan['summary']) ? $scan['summary'] : array();
        $issues = array();

        foreach (array('endpoint_checks', 'signal_checks') as $key) {
            if (empty($scan[$key]) || !is_array($scan[$key])) {
                continue;
            }

            foreach ($scan[$key] as $check) {
                if (empty($check['status']) || !in_array($check['status'], array('warn', 'fail'), true)) {
                    continue;
                }

                $issues[] = '[' . strtoupper($check['status']) . '] ' . $check['label'] . ': ' . $check['summary'];
            }
        }

        $recommendations = isset($scan['recommendations']) && is_array($scan['recommendations']) ? $scan['recommendations'] : array();
        $overall_status = self::get_overall_scan_status($summary);

        return array(
            '{{site_name}}' => (string) get_bloginfo('name'),
            '{{site_url}}' => (string) home_url('/'),
            '{{scan_time}}' => isset($scan['time']) ? (string) $scan['time'] : current_time('mysql'),
            '{{trigger}}' => isset($scan['trigger']) ? (string) $scan['trigger'] : 'manual',
            '{{overall_status}}' => strtoupper($overall_status),
            '{{summary}}' => 'Pass ' . (int) (isset($summary['pass']) ? $summary['pass'] : 0) . ' / Warn ' . (int) (isset($summary['warn']) ? $summary['warn'] : 0) . ' / Fail ' . (int) (isset($summary['fail']) ? $summary['fail'] : 0),
            '{{fail_count}}' => (string) (int) (isset($summary['fail']) ? $summary['fail'] : 0),
            '{{warn_count}}' => (string) (int) (isset($summary['warn']) ? $summary['warn'] : 0),
            '{{trend}}' => !empty($scan['trend']['summary']) ? (string) $scan['trend']['summary'] : 'No previous scan',
            '{{issues}}' => !empty($issues) ? implode("\n", $issues) : 'No warn/fail issues.',
            '{{issues_inline}}' => !empty($issues) ? implode(' | ', $issues) : 'No warn/fail issues.',
            '{{recommendations}}' => !empty($recommendations) ? implode("\n", $recommendations) : 'No recommendations.',
            '{{recommendations_inline}}' => !empty($recommendations) ? implode(' | ', $recommendations) : 'No recommendations.',
            '{{site_name_json}}' => wp_json_encode((string) get_bloginfo('name'), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{site_url_json}}' => wp_json_encode((string) home_url('/'), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{scan_time_json}}' => wp_json_encode(isset($scan['time']) ? (string) $scan['time'] : current_time('mysql'), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{trigger_json}}' => wp_json_encode(isset($scan['trigger']) ? (string) $scan['trigger'] : 'manual', JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{overall_status_json}}' => wp_json_encode(strtoupper($overall_status), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{summary_json}}' => wp_json_encode('Pass ' . (int) (isset($summary['pass']) ? $summary['pass'] : 0) . ' / Warn ' . (int) (isset($summary['warn']) ? $summary['warn'] : 0) . ' / Fail ' . (int) (isset($summary['fail']) ? $summary['fail'] : 0), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{trend_json}}' => wp_json_encode(!empty($scan['trend']['summary']) ? (string) $scan['trend']['summary'] : 'No previous scan', JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{issues_json}}' => wp_json_encode(!empty($issues) ? implode("\n", $issues) : 'No warn/fail issues.', JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            '{{recommendations_json}}' => wp_json_encode(!empty($recommendations) ? implode("\n", $recommendations) : 'No recommendations.', JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        );
    }

    private static function apply_notification_template($template, array $context) {
        return strtr((string) $template, $context);
    }

    private static function build_report_payload(array $scan) {
        $settings = self::get_settings();
        return array(
            'plugin_version' => self::VERSION,
            'site_name' => (string) get_bloginfo('name'),
            'site_url' => (string) home_url('/'),
            'exported_at' => current_time('mysql'),
            'integration' => self::get_integration_context(),
            'configuration' => self::get_reportable_settings_summary($settings),
            'last_regeneration' => get_option(self::OPTION_KEY, array()),
            'scan' => $scan,
            'history' => array_slice(self::get_scan_history(), 0, 10),
        );
    }

    private static function get_reportable_settings_summary(array $settings) {
        return array(
            'management_capability' => isset($settings['management_capability']) ? $settings['management_capability'] : 'manage_options',
            'scheduled_scan_enabled' => !empty($settings['scheduled_scan_enabled']),
            'scheduled_scan_frequency' => isset($settings['scheduled_scan_frequency']) ? $settings['scheduled_scan_frequency'] : 'weekly',
            'notification_email_enabled' => !empty($settings['notify_email_enabled']),
            'notification_webhook_enabled' => !empty($settings['notify_webhook_enabled']),
            'cache_purge_enabled' => !empty($settings['cache_purge_enabled']),
            'cache_purge_local_enabled' => !empty($settings['cache_purge_local_enabled']),
            'cache_purge_cloudflare_enabled' => !empty($settings['cache_purge_cloudflare_enabled']),
            'cache_purge_cloudflare_mode' => isset($settings['cache_purge_cloudflare_mode']) ? $settings['cache_purge_cloudflare_mode'] : 'selected',
            'cache_purge_cloudflare_zone_id' => !empty($settings['cache_purge_cloudflare_zone_id']) ? self::mask_secret($settings['cache_purge_cloudflare_zone_id'], 6) : '',
            'cache_purge_cloudflare_api_token' => !empty($settings['cache_purge_cloudflare_api_token']) ? self::mask_secret($settings['cache_purge_cloudflare_api_token'], 4) : '',
            'included_post_types' => isset($settings['included_post_types']) ? array_values((array) $settings['included_post_types']) : array(),
            'included_term_keys' => isset($settings['included_term_keys']) ? array_values((array) $settings['included_term_keys']) : array(),
        );
    }

    private static function mask_secret($value, $visible_suffix = 4) {
        $value = (string) $value;
        if ($value === '') {
            return '';
        }

        $length = strlen($value);
        if ($length <= $visible_suffix) {
            return str_repeat('*', $length);
        }

        return str_repeat('*', $length - $visible_suffix) . substr($value, -1 * $visible_suffix);
    }

    private static function build_json_report(array $report) {
        return wp_json_encode($report, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }

    private static function build_markdown_report(array $report) {
        $scan = isset($report['scan']) && is_array($report['scan']) ? $report['scan'] : array();
        $summary = isset($scan['summary']) && is_array($scan['summary']) ? $scan['summary'] : array();
        $lines = array();
        $lines[] = '# GEO Scan Report';
        $lines[] = '';
        $lines[] = '- Site: ' . (isset($report['site_name']) ? $report['site_name'] : '');
        $lines[] = '- URL: ' . (isset($report['site_url']) ? $report['site_url'] : '');
        $lines[] = '- Exported At: ' . (isset($report['exported_at']) ? $report['exported_at'] : '');
        $lines[] = '- Plugin Version: ' . (isset($report['plugin_version']) ? $report['plugin_version'] : '');
        $lines[] = '- Scan Time: ' . (isset($scan['time']) ? $scan['time'] : '');
        $lines[] = '- Trigger: ' . (isset($scan['trigger']) ? $scan['trigger'] : '');
        $lines[] = '- Summary: Pass ' . (int) (isset($summary['pass']) ? $summary['pass'] : 0) . ' / Warn ' . (int) (isset($summary['warn']) ? $summary['warn'] : 0) . ' / Fail ' . (int) (isset($summary['fail']) ? $summary['fail'] : 0);
        if (!empty($report['last_regeneration']['cache_purge']['status'])) {
            $lines[] = '- Cache Purge: ' . strtoupper((string) $report['last_regeneration']['cache_purge']['status']);
        }
        $lines[] = '';

        foreach (array('endpoint_checks' => 'Endpoint Checks', 'signal_checks' => 'Signal Checks') as $key => $label) {
            if (empty($scan[$key]) || !is_array($scan[$key])) {
                continue;
            }

            $lines[] = '## ' . $label;
            foreach ($scan[$key] as $check) {
                $lines[] = '- [' . strtoupper(isset($check['status']) ? $check['status'] : 'info') . '] ' . (isset($check['label']) ? $check['label'] : '');
                $lines[] = '  - Summary: ' . (isset($check['summary']) ? $check['summary'] : '');
                if (!empty($check['url'])) {
                    $lines[] = '  - URL: ' . $check['url'];
                }
                if (!empty($check['details']) && is_array($check['details'])) {
                    foreach ($check['details'] as $detail) {
                        $lines[] = '  - Detail: ' . $detail;
                    }
                }
                if (!empty($check['suggestion'])) {
                    $lines[] = '  - Suggestion: ' . $check['suggestion'];
                }
            }
            $lines[] = '';
        }

        if (!empty($scan['recommendations']) && is_array($scan['recommendations'])) {
            $lines[] = '## Recommendations';
            foreach ($scan['recommendations'] as $recommendation) {
                $lines[] = '- ' . $recommendation;
            }
            $lines[] = '';
        }

        if (!empty($report['history']) && is_array($report['history'])) {
            $lines[] = '## Recent History';
            foreach ($report['history'] as $entry) {
                $entry_summary = isset($entry['summary']) && is_array($entry['summary']) ? $entry['summary'] : array();
                $lines[] = '- ' . (isset($entry['time']) ? $entry['time'] : '') . ' | ' . (isset($entry['trigger']) ? $entry['trigger'] : 'manual') . ' | Pass ' . (int) (isset($entry_summary['pass']) ? $entry_summary['pass'] : 0) . ' / Warn ' . (int) (isset($entry_summary['warn']) ? $entry_summary['warn'] : 0) . ' / Fail ' . (int) (isset($entry_summary['fail']) ? $entry_summary['fail'] : 0);
            }
            $lines[] = '';
        }

        return implode("\n", $lines);
    }

    private static function build_csv_report(array $report) {
        $handle = fopen('php://temp', 'r+');
        fputcsv($handle, array('section', 'label', 'url', 'status', 'summary', 'details', 'suggestion'));

        $scan = isset($report['scan']) && is_array($report['scan']) ? $report['scan'] : array();
        foreach (array('endpoint_checks' => 'endpoint', 'signal_checks' => 'signal') as $key => $section) {
            if (empty($scan[$key]) || !is_array($scan[$key])) {
                continue;
            }

            foreach ($scan[$key] as $check) {
                fputcsv(
                    $handle,
                    array(
                        $section,
                        isset($check['label']) ? $check['label'] : '',
                        isset($check['url']) ? $check['url'] : '',
                        isset($check['status']) ? $check['status'] : '',
                        isset($check['summary']) ? $check['summary'] : '',
                        !empty($check['details']) && is_array($check['details']) ? implode(' | ', $check['details']) : '',
                        isset($check['suggestion']) ? $check['suggestion'] : '',
                    )
                );
            }
        }

        if (!empty($report['history']) && is_array($report['history'])) {
            foreach ($report['history'] as $entry) {
                $entry_summary = isset($entry['summary']) && is_array($entry['summary']) ? $entry['summary'] : array();
                fputcsv(
                    $handle,
                    array(
                        'history',
                        isset($entry['time']) ? $entry['time'] : '',
                        '',
                        isset($entry['overall_status']) ? $entry['overall_status'] : '',
                        'Pass ' . (int) (isset($entry_summary['pass']) ? $entry_summary['pass'] : 0) . ' / Warn ' . (int) (isset($entry_summary['warn']) ? $entry_summary['warn'] : 0) . ' / Fail ' . (int) (isset($entry_summary['fail']) ? $entry_summary['fail'] : 0),
                        !empty($entry['trend']['summary']) ? $entry['trend']['summary'] : '',
                        '',
                    )
                );
            }
        }

        rewind($handle);
        $csv = stream_get_contents($handle);
        fclose($handle);
        return "\xEF\xBB\xBF" . $csv;
    }

    private static function stream_download($filename, $content_type, $content) {
        nocache_headers();
        header('Content-Type: ' . $content_type);
        header('Content-Disposition: attachment; filename=' . sanitize_file_name($filename));
        echo (string) $content;
        exit;
    }

    private static function build_endpoint_checks(array $robots_rules, $robots_loaded) {
        $specs = array(
            array(
                'label' => 'robots.txt',
                'url' => home_url('/robots.txt'),
                'expected' => array('text/plain'),
            ),
            array(
                'label' => 'sitemap.xml',
                'url' => home_url('/sitemap.xml'),
                'expected' => array('xml'),
            ),
            array(
                'label' => 'sitemap_index.xml',
                'url' => home_url('/sitemap_index.xml'),
                'expected' => array('xml'),
            ),
            array(
                'label' => 'wp-sitemap.xml',
                'url' => home_url('/wp-sitemap.xml'),
                'expected' => array('xml'),
            ),
            array(
                'label' => 'llms.txt',
                'url' => home_url('/llms.txt'),
                'expected' => array('text/plain'),
            ),
            array(
                'label' => 'llms-full.txt',
                'url' => home_url('/llms-full.txt'),
                'expected' => array('text/plain'),
            ),
        );

        $checks = array();
        foreach ($specs as $spec) {
            $response = self::fetch_url($spec['url']);
            $path = wp_parse_url($spec['url'], PHP_URL_PATH);
            $allowed = $robots_loaded ? self::is_path_allowed_by_robots($robots_rules, $path) : null;
            $content_type = isset($response['content_type']) ? $response['content_type'] : '';
            $expected_ok = self::content_type_matches($content_type, $spec['expected']);

            $status = 'pass';
            $summary = '返回正常。';
            $details = array();
            $suggestion = '';

            if (!empty($response['error'])) {
                $status = 'fail';
                $summary = '请求失败。';
                $details[] = '错误：' . $response['error_message'];
                $suggestion = '先确认站点能从服务器本机访问自身域名。';
            } else {
                $details[] = 'HTTP ' . (int) $response['status_code'];
                $details[] = 'Content-Type: ' . ($content_type ? $content_type : '未返回');

                if ((int) $response['status_code'] !== 200) {
                    $status = 'fail';
                    $summary = '状态码不是 200。';
                    $suggestion = '检查 Nginx rewrite、Cloudflare 缓存规则和对应文件/路由是否直出。';
                } elseif (!$expected_ok) {
                    $status = 'fail';
                    $summary = 'Content-Type 不符合预期。';
                    $suggestion = '让该端点直接返回纯文本或 XML，不要走 HTML 模板。';
                }

                if ($allowed === false) {
                    $status = 'fail';
                    $summary = 'robots.txt 阻止了抓取。';
                    $details[] = 'Crawlability: blocked';
                    $suggestion = '修改 robots.txt，移除对该端点的 Disallow。';
                } elseif ($allowed === true) {
                    $details[] = 'Crawlability: allowed';
                } else {
                    $details[] = 'Crawlability: unknown';
                    if ($status === 'pass') {
                        $status = 'warn';
                        $summary = '端点可访问，但未能确认抓取规则。';
                        $suggestion = '检查 robots.txt 是否可读取且未被缓存旧版本。';
                    }
                }
            }

            $checks[] = array(
                'label' => $spec['label'],
                'url' => $spec['url'],
                'status' => $status,
                'summary' => $summary,
                'details' => $details,
                'suggestion' => $suggestion,
            );
        }

        return $checks;
    }

    private static function build_signal_checks() {
        $checks = array();
        $home_response = self::fetch_url(home_url('/'));
        $article_post = self::get_latest_public_post();
        $article_response = $article_post ? self::fetch_url(get_permalink($article_post)) : array();

        $checks[] = self::scan_homepage_h1($home_response);
        $checks[] = self::scan_homepage_llms_link($home_response);
        $checks[] = self::scan_homepage_canonical($home_response);
        $checks[] = self::scan_article_canonical($article_post, $article_response);
        $checks[] = self::scan_social_meta($home_response);
        $checks[] = self::scan_og_image($home_response, $article_post, $article_response);
        $checks[] = self::scan_article_schema($article_post, $article_response);
        $checks[] = self::scan_author_page_signal($article_post);
        $checks[] = self::scan_breadcrumb_schema($article_post, $article_response);
        $checks[] = self::scan_soft_404();
        $checks[] = self::scan_noindex_conflicts($home_response, $article_post, $article_response);
        $checks[] = self::scan_fetch_consistency($home_response, $article_post, $article_response);
        $checks[] = self::scan_organization_sameas($home_response);
        $checks[] = self::scan_low_value_noindex();

        return $checks;
    }

    private static function build_recommendations(array $checks) {
        $items = array();
        foreach ($checks as $check) {
            if (empty($check['suggestion'])) {
                continue;
            }
            if (!in_array($check['status'], array('warn', 'fail'), true)) {
                continue;
            }
            if (!in_array($check['suggestion'], $items, true)) {
                $items[] = $check['suggestion'];
            }
        }
        return $items;
    }

    private static function scan_homepage_h1($response) {
        $details = array();

        if (!empty($response['error'])) {
            return array(
                'label' => '首页 H1',
                'status' => 'fail',
                'summary' => '首页无法抓取，无法检查 H1。',
                'details' => array('错误：' . $response['error_message']),
                'suggestion' => '先让首页返回稳定的 200 HTML，再补模板中的主 H1。',
            );
        }

        $count = self::count_h1_elements($response['body']);
        $details[] = '检测到 H1 数量：' . (int) $count;

        if ($count === 1) {
            return array(
                'label' => '首页 H1',
                'status' => 'pass',
                'summary' => '首页存在单一主 H1。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        if ($count > 1) {
            return array(
                'label' => '首页 H1',
                'status' => 'warn',
                'summary' => '首页存在多个 H1。',
                'details' => $details,
                'suggestion' => '把首页收敛成一个主 H1，其他大标题改成 H2/H3。',
            );
        }

        return array(
            'label' => '首页 H1',
            'status' => 'fail',
            'summary' => '首页没有 H1。',
            'details' => $details,
            'suggestion' => '在首页模板里补一个真实可见的主 H1，不要用隐藏文本代替。',
        );
    }

    private static function scan_homepage_llms_link($response) {
        if (!empty($response['error'])) {
            return array(
                'label' => '首页 LLMS Link',
                'status' => 'fail',
                'summary' => '首页无法抓取，无法检查 llms link 标签。',
                'details' => array('错误：' . $response['error_message']),
                'suggestion' => '先让首页恢复稳定抓取，再确认 <head> 中输出 <link rel="llms" href="/llms.txt">。',
            );
        }

        $href = self::extract_link_tag_href($response['body'], 'llms');
        $expected_path = self::get_llms_href_path();
        $details = array('检测到 href: ' . ($href !== '' ? $href : '无'));

        if ($href === $expected_path) {
            return array(
                'label' => '首页 LLMS Link',
                'status' => 'pass',
                'summary' => '首页已声明 llms.txt 位置。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        if ($href !== '') {
            return array(
                'label' => '首页 LLMS Link',
                'status' => 'warn',
                'summary' => '首页存在 llms link 标签，但 href 不是预期路径。',
                'details' => $details,
                'suggestion' => '把 <link rel="llms"> 的 href 改成站点 llms.txt 的真实路径。',
            );
        }

        return array(
            'label' => '首页 LLMS Link',
            'status' => 'fail',
            'summary' => '首页缺少 llms link 标签。',
            'details' => $details,
            'suggestion' => '在首页 <head> 里输出 <link rel="llms" href="/llms.txt">。',
        );
    }

    private static function scan_homepage_canonical($response) {
        if (!empty($response['error'])) {
            return array(
                'label' => '首页 Canonical',
                'status' => 'fail',
                'summary' => '首页无法抓取，无法检查 canonical。',
                'details' => array('错误：' . $response['error_message']),
                'suggestion' => '先让首页稳定返回 200，再检查 <link rel="canonical">。',
            );
        }

        $href = self::extract_link_tag_href($response['body'], 'canonical');
        $expected = home_url('/');
        $details = array(
            '检测到 canonical: ' . ($href !== '' ? $href : '无'),
            '预期 canonical: ' . $expected,
        );

        if ($href === '') {
            return array(
                'label' => '首页 Canonical',
                'status' => 'warn',
                'summary' => '首页缺少 canonical。',
                'details' => $details,
                'suggestion' => '为首页补 canonical，避免首页与分页/参数页信号分散。',
            );
        }

        if (self::urls_equivalent($href, $expected)) {
            return array(
                'label' => '首页 Canonical',
                'status' => 'pass',
                'summary' => '首页 canonical 正常。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        return array(
            'label' => '首页 Canonical',
            'status' => 'fail',
            'summary' => '首页 canonical 指向异常。',
            'details' => $details,
            'suggestion' => '把首页 canonical 指向站点首页本身，不要指向参数页、分页页或其他 URL。',
        );
    }

    private static function scan_article_canonical($post, $response) {
        if (!$post instanceof WP_Post) {
            return array(
                'label' => '文章 Canonical',
                'status' => 'info',
                'summary' => '站内没有已发布文章，跳过 canonical 检查。',
                'details' => array(),
                'suggestion' => '',
            );
        }

        if (!empty($response['error'])) {
            return array(
                'label' => '文章 Canonical',
                'status' => 'fail',
                'summary' => '示例文章无法抓取，无法检查 canonical。',
                'details' => array('文章 URL: ' . get_permalink($post), '错误：' . $response['error_message']),
                'suggestion' => '先确认文章页抓取正常，再检查 <link rel="canonical">。',
            );
        }

        $href = self::extract_link_tag_href($response['body'], 'canonical');
        $expected = get_permalink($post);
        $details = array(
            '文章 URL: ' . $expected,
            '检测到 canonical: ' . ($href !== '' ? $href : '无'),
        );

        if ($href === '') {
            return array(
                'label' => '文章 Canonical',
                'status' => 'warn',
                'summary' => '文章页缺少 canonical。',
                'details' => $details,
                'suggestion' => '为文章页补 canonical，避免 AMP/参数页/追踪参数分散信号。',
            );
        }

        if (self::urls_equivalent($href, $expected)) {
            return array(
                'label' => '文章 Canonical',
                'status' => 'pass',
                'summary' => '文章 canonical 正常。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        return array(
            'label' => '文章 Canonical',
            'status' => 'fail',
            'summary' => '文章 canonical 与真实 URL 不一致。',
            'details' => $details,
            'suggestion' => '把 canonical 收敛到文章规范 URL，避免指向首页、分类页或旧 slug。',
        );
    }

    private static function scan_social_meta($response) {
        $details = array();
        $seo_plugin = self::detect_supported_seo_plugin();

        if (!empty($response['error'])) {
            return array(
                'label' => '首页 OG / Twitter',
                'status' => 'fail',
                'summary' => '首页无法抓取，无法检查社交标签。',
                'details' => array('错误：' . $response['error_message']),
                'suggestion' => '先解决首页抓取问题，再补 OG/Twitter 标签。',
            );
        }

        $meta = self::extract_meta_tags($response['body']);
        $og_required = array('og:title', 'og:description', 'og:url', 'og:type');
        $twitter_required = array('twitter:card', 'twitter:title', 'twitter:description');

        $missing_og = self::missing_meta_keys($meta, $og_required);
        $missing_twitter = self::missing_meta_keys($meta, $twitter_required);

        $details[] = '缺失 OG: ' . (!empty($missing_og) ? implode(', ', $missing_og) : '无');
        $details[] = '缺失 Twitter: ' . (!empty($missing_twitter) ? implode(', ', $missing_twitter) : '无');

        if (empty($missing_og) && empty($missing_twitter)) {
            return array(
                'label' => '首页 OG / Twitter',
                'status' => 'pass',
                'summary' => '首页已具备基础 OG/Twitter 标签。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        $status = (count($missing_og) + count($missing_twitter) >= 4) ? 'fail' : 'warn';
        return array(
            'label' => '首页 OG / Twitter',
            'status' => $status,
            'summary' => '首页社交分享标签不完整。',
            'details' => $details,
            'suggestion' => $seo_plugin
                ? '检测到 ' . $seo_plugin . '。如果标签仍缺失，优先检查该插件配置，以及主题是否正确调用 wp_head()。'
                : '启用插件里的基础 OG/Twitter 补全，或在主题里补齐标题、描述、URL、卡片类型。',
        );
    }

    private static function scan_og_image($home_response, $article_post, $article_response) {
        $details = array();
        $missing = array();

        if (!empty($home_response['error'])) {
            return array(
                'label' => 'OG Image',
                'status' => 'fail',
                'summary' => '首页无法抓取，无法检查 og:image。',
                'details' => array('错误：' . $home_response['error_message']),
                'suggestion' => '先解决首页抓取问题，再检查 og:image / twitter:image。',
            );
        }

        $home_meta = self::extract_meta_tags($home_response['body']);
        $home_ok = !empty($home_meta['og:image']) || !empty($home_meta['twitter:image']);
        $details[] = '首页图片：' . ($home_ok ? '已检测到' : '缺失');
        if (!$home_ok) {
            $missing[] = '首页';
        }

        if ($article_post instanceof WP_Post) {
            if (!empty($article_response['error'])) {
                $details[] = '文章图片：抓取失败';
                $missing[] = '文章页';
            } else {
                $article_meta = self::extract_meta_tags($article_response['body']);
                $article_ok = !empty($article_meta['og:image']) || !empty($article_meta['twitter:image']);
                $details[] = '文章图片：' . ($article_ok ? '已检测到' : '缺失');
                if (!$article_ok) {
                    $missing[] = '文章页';
                }
            }
        }

        if (empty($missing)) {
            return array(
                'label' => 'OG Image',
                'status' => 'pass',
                'summary' => '首页和文章页都检测到了分享图片。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        return array(
            'label' => 'OG Image',
            'status' => count($missing) >= 2 ? 'fail' : 'warn',
            'summary' => '部分高价值页缺少 og:image / twitter:image。',
            'details' => $details,
            'suggestion' => '为首页和文章页补默认分享图。文章优先使用特色图，首页优先使用品牌 logo 或封面图。',
        );
    }

    private static function scan_article_schema($post, $response) {
        $seo_plugin = self::detect_supported_seo_plugin();
        if (!$post instanceof WP_Post) {
            return array(
                'label' => '文章 Article Schema',
                'status' => 'info',
                'summary' => '站内没有已发布文章，跳过 Article schema 检查。',
                'details' => array(),
                'suggestion' => '',
            );
        }

        if (!empty($response['error'])) {
            return array(
                'label' => '文章 Article Schema',
                'status' => 'fail',
                'summary' => '示例文章无法抓取，无法检查 schema。',
                'details' => array('文章 URL: ' . get_permalink($post), '错误：' . $response['error_message']),
                'suggestion' => '先确认文章页能正常返回，再检查 JSON-LD。',
            );
        }

        $nodes = self::extract_json_ld_nodes($response['body']);
        $node = self::find_json_ld_node_by_type($nodes, array('Article', 'BlogPosting', 'NewsArticle'));

        if (!$node) {
            return array(
                'label' => '文章 Article Schema',
                'status' => 'fail',
                'summary' => '未检测到 Article / BlogPosting / NewsArticle JSON-LD。',
                'details' => array('文章 URL: ' . get_permalink($post)),
                'suggestion' => $seo_plugin
                    ? '检测到 ' . $seo_plugin . '，但文章页仍没有 Article schema。优先检查该插件的 schema 开关和单篇文章设置。'
                    : '补 Article schema，至少包含 author、datePublished、dateModified、publisher。',
            );
        }

        $missing = array();
        foreach (array('author', 'datePublished', 'dateModified', 'publisher') as $field) {
            if (empty($node[$field])) {
                $missing[] = $field;
            }
        }

        $details = array('文章 URL: ' . get_permalink($post));
        $details[] = '缺失字段：' . (!empty($missing) ? implode(', ', $missing) : '无');

        if (empty($missing)) {
            return array(
                'label' => '文章 Article Schema',
                'status' => 'pass',
                'summary' => '文章 schema 字段完整。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        return array(
            'label' => '文章 Article Schema',
            'status' => count($missing) >= 3 ? 'fail' : 'warn',
            'summary' => '文章 schema 不完整。',
            'details' => $details,
            'suggestion' => $seo_plugin
                ? '检测到 ' . $seo_plugin . '。如果字段仍缺失，优先检查该插件的文章 schema 配置。'
                : '补 Article schema，至少包含 author、datePublished、dateModified、publisher。',
        );
    }

    private static function scan_author_page_signal($post) {
        if (!$post instanceof WP_Post) {
            return array(
                'label' => '作者页信号',
                'status' => 'info',
                'summary' => '站内没有已发布文章，跳过作者页检查。',
                'details' => array(),
                'suggestion' => '',
            );
        }

        $author_url = get_author_posts_url($post->post_author);
        $response = self::fetch_url($author_url);
        if (!empty($response['error'])) {
            return array(
                'label' => '作者页信号',
                'status' => 'fail',
                'summary' => '作者页无法抓取。',
                'details' => array('作者页: ' . $author_url, '错误：' . $response['error_message']),
                'suggestion' => '确认作者页未被主题或权限逻辑禁掉，并能返回 200 HTML。',
            );
        }

        $canonical = self::extract_link_tag_href($response['body'], 'canonical');
        $h1_count = self::count_h1_elements($response['body']);
        $is_noindex = self::response_is_noindex($response);
        $details = array(
            '作者页: ' . $author_url,
            'HTTP ' . (int) $response['status_code'],
            'canonical: ' . ($canonical !== '' ? $canonical : '无'),
            'H1 数量: ' . $h1_count,
            'noindex: ' . ($is_noindex ? '是' : '否'),
        );

        if ((int) $response['status_code'] !== 200) {
            return array(
                'label' => '作者页信号',
                'status' => 'fail',
                'summary' => '作者页状态码异常。',
                'details' => $details,
                'suggestion' => '让作者页稳定返回 200，避免作者实体信号完全丢失。',
            );
        }

        if ($is_noindex) {
            return array(
                'label' => '作者页信号',
                'status' => 'warn',
                'summary' => '作者页被 noindex。',
                'details' => $details,
                'suggestion' => '如果作者页承载品牌/作者信号，建议移除 noindex，并补简介、头像和内容列表。',
            );
        }

        if ($h1_count !== 1 || $canonical === '') {
            return array(
                'label' => '作者页信号',
                'status' => 'warn',
                'summary' => '作者页信号不完整。',
                'details' => $details,
                'suggestion' => '作者页建议保留单一 H1，并输出 canonical，形成稳定作者实体页。',
            );
        }

        return array(
            'label' => '作者页信号',
            'status' => 'pass',
            'summary' => '作者页可抓取且基础信号正常。',
            'details' => $details,
            'suggestion' => '',
        );
    }

    private static function scan_breadcrumb_schema($post, $response) {
        if (!$post instanceof WP_Post) {
            return array(
                'label' => 'Breadcrumb Schema',
                'status' => 'info',
                'summary' => '站内没有已发布文章，跳过 breadcrumb 检查。',
                'details' => array(),
                'suggestion' => '',
            );
        }

        if (!empty($response['error'])) {
            return array(
                'label' => 'Breadcrumb Schema',
                'status' => 'fail',
                'summary' => '示例文章无法抓取，无法检查 BreadcrumbList。',
                'details' => array('文章 URL: ' . get_permalink($post), '错误：' . $response['error_message']),
                'suggestion' => '先让文章页抓取正常，再检查 breadcrumb schema。',
            );
        }

        $nodes = self::extract_json_ld_nodes($response['body']);
        $node = self::find_json_ld_node_by_type($nodes, array('BreadcrumbList'));
        if (!$node) {
            return array(
                'label' => 'Breadcrumb Schema',
                'status' => self::detect_supported_seo_plugin() ? 'warn' : 'fail',
                'summary' => '文章页未检测到 BreadcrumbList JSON-LD。',
                'details' => array('文章 URL: ' . get_permalink($post)),
                'suggestion' => '补 BreadcrumbList schema，帮助搜索引擎理解内容层级。',
            );
        }

        $count = 0;
        if (!empty($node['itemListElement']) && is_array($node['itemListElement'])) {
            $count = count($node['itemListElement']);
        }

        if ($count < 2) {
            return array(
                'label' => 'Breadcrumb Schema',
                'status' => 'warn',
                'summary' => 'BreadcrumbList 存在，但层级过少。',
                'details' => array('itemListElement 数量: ' . $count),
                'suggestion' => '至少保留首页 -> 分类/栏目 -> 文章 这样的最小 breadcrumb 层级。',
            );
        }

        return array(
            'label' => 'Breadcrumb Schema',
            'status' => 'pass',
            'summary' => '文章页已输出 BreadcrumbList。',
            'details' => array('itemListElement 数量: ' . $count),
            'suggestion' => '',
        );
    }

    private static function scan_soft_404() {
        $probe_url = home_url('/geo-soft-404-probe-' . wp_generate_password(10, false, false) . '/');
        $response = self::fetch_url($probe_url, array('redirection' => 0));

        if (!empty($response['error'])) {
            return array(
                'label' => 'Soft 404',
                'status' => 'warn',
                'summary' => '探测 URL 请求失败，无法确认 404 行为。',
                'details' => array('探测 URL: ' . $probe_url, '错误：' . $response['error_message']),
                'suggestion' => '检查站点本机回源和 Cloudflare 是否拦截了异常路径探测。',
            );
        }

        $status_code = (int) $response['status_code'];
        $location = isset($response['headers']['location']) ? (string) $response['headers']['location'] : '';
        $details = array(
            '探测 URL: ' . $probe_url,
            'HTTP ' . $status_code,
            'Location: ' . ($location !== '' ? $location : '无'),
        );

        if (in_array($status_code, array(404, 410), true)) {
            return array(
                'label' => 'Soft 404',
                'status' => 'pass',
                'summary' => '不存在的 URL 能正确返回 404/410。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        if ($status_code >= 300 && $status_code < 400) {
            return array(
                'label' => 'Soft 404',
                'status' => 'fail',
                'summary' => '不存在的 URL 被重定向，疑似 soft 404。',
                'details' => $details,
                'suggestion' => '不要把不存在的路径统一 301/302 到首页；让它返回真实 404 模板。',
            );
        }

        if ($status_code === 200) {
            return array(
                'label' => 'Soft 404',
                'status' => 'fail',
                'summary' => '不存在的 URL 返回了 200，属于典型 soft 404。',
                'details' => $details,
                'suggestion' => '检查 Nginx rewrite、主题 404 模板和异常路径回源逻辑，让不存在的路径返回 404。',
            );
        }

        return array(
            'label' => 'Soft 404',
            'status' => 'warn',
            'summary' => '不存在的 URL 返回了非常规状态码。',
            'details' => $details,
            'suggestion' => '确认 404 路径处理是否稳定，避免搜索引擎把错误页当成正常页面。',
        );
    }

    private static function scan_noindex_conflicts($home_response, $article_post, $article_response) {
        $details = array();
        $conflicts = array();

        if (!empty($home_response['error'])) {
            $details[] = '首页抓取失败';
        } elseif (self::response_is_noindex($home_response)) {
            $conflicts[] = '首页';
        } else {
            $details[] = '首页：未检测到 noindex';
        }

        if ($article_post instanceof WP_Post) {
            if (!empty($article_response['error'])) {
                $details[] = '文章页抓取失败';
            } elseif (self::response_is_noindex($article_response)) {
                $conflicts[] = '文章页';
            } else {
                $details[] = '文章页：未检测到 noindex';
            }
        }

        if (empty($conflicts)) {
            return array(
                'label' => '高价值页 noindex 冲突',
                'status' => 'pass',
                'summary' => '首页和文章页没有发现 noindex 冲突。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        return array(
            'label' => '高价值页 noindex 冲突',
            'status' => 'fail',
            'summary' => '高价值页被 noindex。',
            'details' => array_merge($details, array('冲突页面：' . implode(' / ', $conflicts))),
            'suggestion' => '优先排查 SEO 插件、主题自定义头部输出和服务器 X-Robots-Tag，移除首页/文章页的 noindex。',
        );
    }

    private static function scan_fetch_consistency($home_response, $article_post, $article_response) {
        $googlebot_ua = self::get_googlebot_user_agent();
        $home_bot_response = self::fetch_url(home_url('/'), array('user_agent' => $googlebot_ua));
        $comparisons = array();
        $issues = array();

        $comparisons[] = self::compare_fetch_variants('首页', $home_response, $home_bot_response);

        if ($article_post instanceof WP_Post) {
            $article_bot_response = self::fetch_url(get_permalink($article_post), array('user_agent' => $googlebot_ua));
            $comparisons[] = self::compare_fetch_variants('文章页', $article_response, $article_bot_response);
        }

        foreach ($comparisons as $comparison) {
            if (!empty($comparison['problem'])) {
                $issues[] = $comparison['problem'];
            }
        }

        $details = array();
        foreach ($comparisons as $comparison) {
            if (!empty($comparison['detail'])) {
                $details[] = $comparison['detail'];
            }
        }

        if (empty($issues)) {
            return array(
                'label' => '首页/文章页抓取一致性',
                'status' => 'pass',
                'summary' => '默认请求与 Googlebot UA 的抓取结果基本一致。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        $has_fail = false;
        foreach ($comparisons as $comparison) {
            if (!empty($comparison['status']) && $comparison['status'] === 'fail') {
                $has_fail = true;
                break;
            }
        }

        return array(
            'label' => '首页/文章页抓取一致性',
            'status' => $has_fail ? 'fail' : 'warn',
            'summary' => '默认请求与 Googlebot UA 的抓取结果不一致。',
            'details' => array_merge($details, $issues),
            'suggestion' => '检查 Cloudflare Bot/WAF 规则、缓存旁路和服务器 UA 分流，确保搜索引擎与普通访问者拿到相同页面。',
        );
    }

    private static function scan_organization_sameas($response) {
        if (!empty($response['error'])) {
            return array(
                'label' => 'Organization sameAs',
                'status' => 'fail',
                'summary' => '首页无法抓取，无法检查 Organization schema。',
                'details' => array('错误：' . $response['error_message']),
                'suggestion' => '先解决首页抓取问题，再补 Organization.sameAs。',
            );
        }

        $nodes = self::extract_json_ld_nodes($response['body']);
        $node = self::find_json_ld_node_by_type($nodes, array('Organization'));

        if (!$node) {
            return array(
                'label' => 'Organization sameAs',
                'status' => 'warn',
                'summary' => '首页未检测到 Organization JSON-LD。',
                'details' => array(),
                'suggestion' => '在插件设置里填写 sameAs 链接，并启用基础 schema 补全；或在主题/SEO 插件中输出 Organization。',
            );
        }

        $sameas = isset($node['sameAs']) ? $node['sameAs'] : array();
        if (is_string($sameas)) {
            $sameas = array($sameas);
        }
        $sameas = array_values(array_filter((array) $sameas));

        if (!empty($sameas)) {
            return array(
                'label' => 'Organization sameAs',
                'status' => 'pass',
                'summary' => 'Organization.sameAs 已配置。',
                'details' => array('链接数量：' . count($sameas)),
                'suggestion' => '',
            );
        }

        return array(
            'label' => 'Organization sameAs',
            'status' => 'fail',
            'summary' => 'Organization 存在，但 sameAs 为空。',
            'details' => array(),
            'suggestion' => '在插件设置里补上 YouTube / X / 小红书 / B 站 / Telegram 等品牌账号链接。',
        );
    }

    private static function scan_low_value_noindex() {
        $checks = self::get_low_value_targets_for_scan();
        if (empty($checks)) {
            return array(
                'label' => '低价值页 noindex',
                'status' => 'info',
                'summary' => '未找到可检查的低价值页。',
                'details' => array(),
                'suggestion' => '',
            );
        }

        $details = array();
        $needs_fix = array();

        foreach ($checks as $item) {
            $response = self::fetch_url($item['url']);
            if (!empty($response['error'])) {
                $details[] = $item['label'] . ': 请求失败';
                continue;
            }

            if ((int) $response['status_code'] >= 400) {
                $details[] = $item['label'] . ': HTTP ' . (int) $response['status_code'] . '（跳过）';
                continue;
            }

            if (self::response_is_noindex($response)) {
                $details[] = $item['label'] . ': 已 noindex';
            } else {
                $details[] = $item['label'] . ': 缺少 noindex';
                $needs_fix[] = $item['label'];
            }
        }

        if (empty($needs_fix)) {
            return array(
                'label' => '低价值页 noindex',
                'status' => 'pass',
                'summary' => '已检查的低价值页都带 noindex。',
                'details' => $details,
                'suggestion' => '',
            );
        }

        return array(
            'label' => '低价值页 noindex',
            'status' => 'warn',
            'summary' => '部分低价值页还没有 noindex。',
            'details' => $details,
            'suggestion' => '启用插件里的低价值页 noindex；若是自定义示例页，确认 slug 或标题命中规则。',
        );
    }

    private static function get_low_value_targets_for_scan() {
        $items = array(
            array(
                'label' => '登录页',
                'url' => wp_login_url(),
            ),
            array(
                'label' => '找回密码页',
                'url' => add_query_arg('action', 'lostpassword', wp_login_url()),
            ),
            array(
                'label' => '搜索页',
                'url' => add_query_arg('s', 'geo-audit-probe', home_url('/')),
            ),
        );

        if (function_exists('wc_get_page_permalink')) {
            foreach (array('cart' => '购物车页', 'checkout' => '结账页', 'myaccount' => '账户页') as $key => $label) {
                $url = wc_get_page_permalink($key);
                if ($url) {
                    $items[] = array(
                        'label' => $label,
                        'url' => $url,
                    );
                }
            }
        }

        $sample_page = self::find_sample_page();
        if ($sample_page instanceof WP_Post) {
            $items[] = array(
                'label' => '示例页',
                'url' => get_permalink($sample_page),
            );
        }

        return $items;
    }

    private static function fetch_url($url, array $args = array()) {
        $headers = array(
            'Accept' => 'text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8',
        );

        if (!empty($args['user_agent'])) {
            $headers['User-Agent'] = (string) $args['user_agent'];
        }

        $response = wp_remote_get(
            $url,
            array(
                'timeout' => isset($args['timeout']) ? (int) $args['timeout'] : 15,
                'redirection' => isset($args['redirection']) ? (int) $args['redirection'] : 4,
                'limit_response_size' => isset($args['limit_response_size']) ? (int) $args['limit_response_size'] : 262144,
                'headers' => $headers,
                'user-agent' => !empty($args['user_agent']) ? (string) $args['user_agent'] : 'WordPress/' . get_bloginfo('version') . '; ' . home_url('/'),
            )
        );

        if (is_wp_error($response)) {
            return array(
                'error' => true,
                'error_message' => $response->get_error_message(),
                'status_code' => 0,
                'content_type' => '',
                'body' => '',
                'headers' => array(),
            );
        }

        $headers = wp_remote_retrieve_headers($response);
        $header_map = array();
        foreach ($headers as $key => $value) {
            $header_map[strtolower($key)] = is_array($value) ? implode(', ', $value) : (string) $value;
        }

        return array(
            'error' => false,
            'error_message' => '',
            'status_code' => (int) wp_remote_retrieve_response_code($response),
            'content_type' => isset($header_map['content-type']) ? strtolower(trim($header_map['content-type'])) : '',
            'body' => (string) wp_remote_retrieve_body($response),
            'headers' => $header_map,
        );
    }

    private static function content_type_matches($content_type, array $expected_parts) {
        if ($content_type === '') {
            return false;
        }

        foreach ($expected_parts as $part) {
            if (strpos($content_type, strtolower($part)) !== false) {
                return true;
            }
        }

        return false;
    }

    private static function parse_robots_txt($body) {
        $rules = array();
        $agents = array();
        $last_directive = '';
        $lines = preg_split('/\r\n|\r|\n/', (string) $body);

        foreach ($lines as $line) {
            $line = preg_replace('/\s*#.*$/', '', trim($line));
            if ($line === '' || strpos($line, ':') === false) {
                continue;
            }

            list($directive, $value) = array_map('trim', explode(':', $line, 2));
            $directive = strtolower($directive);
            $value = trim($value);

            if ($directive === 'user-agent') {
                if ($last_directive !== 'user-agent') {
                    $agents = array();
                }
                $agents[] = strtolower($value);
                $last_directive = 'user-agent';
                continue;
            }

            if (!in_array($directive, array('allow', 'disallow'), true)) {
                $last_directive = $directive;
                continue;
            }

            if (empty($agents)) {
                $agents = array('*');
            }

            foreach ($agents as $agent) {
                if (!isset($rules[$agent])) {
                    $rules[$agent] = array();
                }
                $rules[$agent][] = array(
                    'directive' => $directive,
                    'pattern' => $value,
                );
            }

            $last_directive = $directive;
        }

        return $rules;
    }

    private static function is_path_allowed_by_robots(array $rules, $path) {
        if ($path === '') {
            return null;
        }

        if (empty($rules)) {
            return true;
        }

        $candidates = array();
        foreach (array('googlebot', '*') as $agent) {
            if (!empty($rules[$agent]) && is_array($rules[$agent])) {
                $candidates = array_merge($candidates, $rules[$agent]);
            }
        }

        if (empty($candidates)) {
            return null;
        }

        $matched = null;
        $matched_len = -1;

        foreach ($candidates as $rule) {
            $pattern = isset($rule['pattern']) ? $rule['pattern'] : '';
            if ($pattern === '') {
                continue;
            }
            if (!self::robots_pattern_matches($pattern, $path)) {
                continue;
            }
            $len = strlen($pattern);
            if ($len >= $matched_len) {
                $matched = $rule;
                $matched_len = $len;
            }
        }

        if (!$matched) {
            return true;
        }

        return $matched['directive'] === 'allow';
    }

    private static function robots_pattern_matches($pattern, $path) {
        $regex = preg_quote((string) $pattern, '/');
        $regex = str_replace('\*', '.*', $regex);
        if (substr($regex, -2) === '\$') {
            $regex = substr($regex, 0, -2) . '$';
        } else {
            $regex .= '.*';
        }
        return (bool) preg_match('/^' . $regex . '/i', $path);
    }

    private static function count_h1_elements($html) {
        if (class_exists('DOMDocument')) {
            $dom = new DOMDocument();
            libxml_use_internal_errors(true);
            $loaded = $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html);
            libxml_clear_errors();
            if ($loaded) {
                $xpath = new DOMXPath($dom);
                return (int) $xpath->query('//h1')->length;
            }
        }

        return preg_match_all('/<h1\b/i', (string) $html, $matches);
    }

    private static function extract_meta_tags($html) {
        $items = array();

        if (class_exists('DOMDocument')) {
            $dom = new DOMDocument();
            libxml_use_internal_errors(true);
            $loaded = $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html);
            libxml_clear_errors();
            if ($loaded) {
                $meta_nodes = $dom->getElementsByTagName('meta');
                foreach ($meta_nodes as $node) {
                    if (!$node instanceof DOMElement) {
                        continue;
                    }
                    foreach (array('name', 'property') as $attr) {
                        if ($node->hasAttribute($attr)) {
                            $key = strtolower(trim($node->getAttribute($attr)));
                            if ($key !== '') {
                                $items[$key] = trim($node->getAttribute('content'));
                            }
                        }
                    }
                }
                return $items;
            }
        }

        if (preg_match_all('/<meta[^>]+(?:name|property)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\'][^>]*>/i', $html, $matches, PREG_SET_ORDER)) {
            foreach ($matches as $match) {
                $items[strtolower(trim($match[1]))] = trim($match[2]);
            }
        }

        return $items;
    }

    private static function missing_meta_keys(array $meta, array $keys) {
        $missing = array();
        foreach ($keys as $key) {
            if (empty($meta[strtolower($key)])) {
                $missing[] = $key;
            }
        }
        return $missing;
    }

    private static function extract_json_ld_nodes($html) {
        $nodes = array();
        if (!preg_match_all('/<script[^>]+type=["\']application\/ld\+json["\'][^>]*>(.*?)<\/script>/is', $html, $matches)) {
            return $nodes;
        }

        foreach ($matches[1] as $block) {
            $block = trim((string) $block);
            if ($block === '') {
                continue;
            }
            $data = json_decode($block, true);
            if (json_last_error() !== JSON_ERROR_NONE) {
                continue;
            }
            self::flatten_json_ld_node($data, $nodes);
        }

        return $nodes;
    }

    private static function flatten_json_ld_node($data, array &$nodes) {
        if (!is_array($data)) {
            return;
        }

        if (self::is_assoc_array($data)) {
            if (isset($data['@graph']) && is_array($data['@graph'])) {
                foreach ($data['@graph'] as $graph_node) {
                    self::flatten_json_ld_node($graph_node, $nodes);
                }
            }

            if (!empty($data['@type'])) {
                $nodes[] = $data;
            }
            return;
        }

        foreach ($data as $item) {
            self::flatten_json_ld_node($item, $nodes);
        }
    }

    private static function find_json_ld_node_by_type(array $nodes, array $types) {
        foreach ($nodes as $node) {
            if (!is_array($node) || empty($node['@type'])) {
                continue;
            }

            $node_types = is_array($node['@type']) ? $node['@type'] : array($node['@type']);
            foreach ($node_types as $node_type) {
                if (in_array($node_type, $types, true)) {
                    return $node;
                }
            }
        }

        return null;
    }

    private static function is_assoc_array(array $value) {
        return array_keys($value) !== range(0, count($value) - 1);
    }

    private static function get_latest_public_post() {
        $posts = get_posts(
            array(
                'post_type' => 'post',
                'post_status' => 'publish',
                'posts_per_page' => 1,
                'orderby' => 'date',
                'order' => 'DESC',
                'no_found_rows' => true,
            )
        );

        if (empty($posts[0]) || !($posts[0] instanceof WP_Post)) {
            return null;
        }

        return $posts[0];
    }

    private static function response_is_noindex($response) {
        if (!empty($response['headers']['x-robots-tag']) && stripos($response['headers']['x-robots-tag'], 'noindex') !== false) {
            return true;
        }

        $meta = self::extract_meta_tags(isset($response['body']) ? $response['body'] : '');
        foreach (array('robots', 'googlebot') as $key) {
            if (!empty($meta[$key]) && stripos($meta[$key], 'noindex') !== false) {
                return true;
            }
        }

        return false;
    }

    private static function should_exclude_post_from_llms($post) {
        if (!$post instanceof WP_Post) {
            return true;
        }

        $settings = self::get_settings();
        if ($post->post_status !== 'publish') {
            return true;
        }

        $title = trim(wp_strip_all_tags(get_the_title($post), true));
        if ($title === '') {
            return true;
        }

        if ((string) get_post_meta($post->ID, self::META_EXCLUDE_KEY, true) === '1') {
            return true;
        }

        if (self::post_matches_ref_rules($post, self::get_ref_lines(isset($settings['excluded_refs']) ? $settings['excluded_refs'] : ''))) {
            return true;
        }

        if (!empty($settings['exclude_low_value_from_llms']) && self::is_low_value_post($post)) {
            return true;
        }

        return false;
    }

    private static function post_matches_ref_rules($post, array $rules) {
        if (!$post instanceof WP_Post || empty($rules)) {
            return false;
        }

        $permalink = get_permalink($post);
        $path = self::normalize_path($permalink);
        $slug = sanitize_title($post->post_name);
        $post_id = (string) $post->ID;

        foreach ($rules as $rule) {
            $rule = trim((string) $rule);
            if ($rule === '') {
                continue;
            }

            if ($rule === $post_id || $rule === $slug) {
                return true;
            }

            if ((strpos($rule, 'http://') === 0 || strpos($rule, 'https://') === 0) && untrailingslashit($rule) === untrailingslashit($permalink)) {
                return true;
            }

            if (strpos($rule, '/') === 0 && self::normalize_path($rule) === $path) {
                return true;
            }
        }

        return false;
    }

    private static function normalize_path($value) {
        $path = wp_parse_url((string) $value, PHP_URL_PATH);
        if (!is_string($path) || $path === '') {
            return '/';
        }

        $path = '/' . ltrim($path, '/');
        return untrailingslashit($path) ?: '/';
    }

    private static function is_low_value_post($post) {
        if (!$post instanceof WP_Post) {
            return false;
        }

        $title = trim(wp_strip_all_tags(get_the_title($post), true));
        $slug = isset($post->post_name) ? sanitize_title($post->post_name) : '';

        if ($title === '') {
            return true;
        }

        if (self::is_low_value_slug($slug)) {
            return true;
        }

        if (self::is_low_value_title($title)) {
            return true;
        }

        return false;
    }

    private static function is_low_value_slug($slug) {
        $slug = sanitize_title((string) $slug);
        if ($slug === '') {
            return false;
        }

        $patterns = array(
            'sample-page',
            'hello-world',
            'login',
            'register',
            'signup',
            'sign-up',
            'forgot-password',
            'lost-password',
            'reset-password',
            'resetpass',
            'my-account',
            'cart',
            'checkout',
            'wishlist',
        );

        foreach ($patterns as $pattern) {
            if ($slug === $pattern || strpos($slug, $pattern . '-') === 0) {
                return true;
            }
        }

        return false;
    }

    private static function is_low_value_title($title) {
        $normalized = strtolower(trim((string) $title));
        $patterns = array(
            'sample page',
            'hello world',
            '示例页',
            '示例页面',
            '登录',
            '注册',
            '找回密码',
            '重置密码',
            '购物车',
            '结账',
            '我的账户',
        );

        return in_array($normalized, $patterns, true);
    }

    private static function find_sample_page() {
        $pages = get_pages(
            array(
                'post_status' => 'publish',
                'sort_column' => 'post_date',
                'sort_order' => 'DESC',
            )
        );

        foreach ($pages as $page) {
            if (!($page instanceof WP_Post)) {
                continue;
            }
            if (self::is_low_value_post($page)) {
                return $page;
            }
        }

        return null;
    }

    public static function filter_wp_robots($robots) {
        if (is_admin()) {
            return $robots;
        }

        if (!self::should_noindex_current_request()) {
            return $robots;
        }

        $robots['noindex'] = true;
        $robots['nofollow'] = true;
        $robots['noarchive'] = true;

        return $robots;
    }

    public static function send_x_robots_header() {
        if (is_admin()) {
            return;
        }

        if (!self::should_noindex_current_request()) {
            return;
        }

        header('X-Robots-Tag: noindex, nofollow, noarchive', true);
    }

    public static function send_login_noindex_header() {
        if (!self::is_login_request()) {
            return;
        }

        $settings = self::get_settings();
        if (empty($settings['enable_low_value_noindex'])) {
            return;
        }

        header('X-Robots-Tag: noindex, nofollow, noarchive', true);
    }

    public static function render_login_noindex_meta() {
        if (!self::is_login_request()) {
            return;
        }

        $settings = self::get_settings();
        if (empty($settings['enable_low_value_noindex'])) {
            return;
        }

        echo "<meta name=\"robots\" content=\"noindex,nofollow,noarchive\" />\n";
    }

    public static function output_llms_link_tag() {
        if (is_admin() || is_feed()) {
            return;
        }

        $settings = self::get_settings();
        if (empty($settings['enable_llms_link_tag'])) {
            return;
        }

        if (!(is_front_page() || is_home())) {
            return;
        }

        printf("<link rel=\"llms\" href=\"%s\" />\n", esc_attr(self::get_llms_href_path()));
    }

    public static function filter_endpoint_canonical_redirect($redirect_url, $requested_url) {
        if (!self::is_wp_endpoint_fix_enabled()) {
            return $redirect_url;
        }

        $path = self::extract_request_path_from_url($requested_url);
        if (!self::is_endpoint_fix_path($path)) {
            return $redirect_url;
        }

        return false;
    }

    public static function maybe_serve_endpoint_fallbacks() {
        if (!self::is_wp_endpoint_fix_enabled()) {
            return;
        }

        $method = isset($_SERVER['REQUEST_METHOD']) ? strtoupper((string) $_SERVER['REQUEST_METHOD']) : 'GET';
        if (!in_array($method, array('GET', 'HEAD'), true)) {
            return;
        }

        $path = self::get_current_request_path();
        if (!self::is_endpoint_fix_path($path)) {
            return;
        }

        if ($path === '/robots.txt') {
            self::serve_robots_endpoint();
            return;
        }

        if ($path === '/wp-sitemap.xml') {
            self::serve_wp_sitemap_endpoint();
            return;
        }

        if ($path === '/sitemap.xml' || $path === '/sitemap_index.xml') {
            self::serve_sitemap_alias_endpoint();
            return;
        }
    }

    private static function is_wp_endpoint_fix_enabled() {
        if (is_admin() || (function_exists('wp_doing_ajax') && wp_doing_ajax())) {
            return false;
        }

        $settings = self::get_settings();
        return !empty($settings['enable_wp_endpoint_fix']);
    }

    private static function extract_request_path_from_url($url) {
        if (!is_string($url) || $url === '') {
            return '/';
        }

        $path = wp_parse_url($url, PHP_URL_PATH);
        return self::normalize_path($path ? $path : '/');
    }

    private static function get_current_request_path() {
        $request_uri = isset($_SERVER['REQUEST_URI']) ? (string) wp_unslash($_SERVER['REQUEST_URI']) : '/';
        $path = wp_parse_url($request_uri, PHP_URL_PATH);
        return self::normalize_path($path ? $path : '/');
    }

    private static function is_endpoint_fix_path($path) {
        return in_array($path, array('/robots.txt', '/sitemap.xml', '/sitemap_index.xml', '/wp-sitemap.xml'), true);
    }

    private static function send_endpoint_response($body, $content_type) {
        status_header(200);
        nocache_headers();
        header('Content-Type: ' . $content_type, true);
        header('X-GEO-Endpoint-Fix: 1', true);

        $method = isset($_SERVER['REQUEST_METHOD']) ? strtoupper((string) $_SERVER['REQUEST_METHOD']) : 'GET';
        if ($method !== 'HEAD') {
            echo (string) $body;
        }
        exit;
    }

    private static function serve_robots_endpoint() {
        $path = trailingslashit(ABSPATH) . 'robots.txt';
        if (is_readable($path)) {
            $body = (string) file_get_contents($path);
            self::send_endpoint_response($body, 'text/plain; charset=UTF-8');
        }

        $public = (bool) get_option('blog_public');
        $body = "User-agent: *\n";
        $body .= $public ? "Allow: /\n" : "Disallow: /\n";
        $body .= 'Sitemap: ' . esc_url_raw(home_url('/sitemap.xml')) . "\n";
        $body = apply_filters('robots_txt', $body, $public);
        self::send_endpoint_response($body, 'text/plain; charset=UTF-8');
    }

    private static function serve_sitemap_alias_endpoint() {
        $body = self::build_sitemap_index_xml(array(home_url('/wp-sitemap.xml')));
        self::send_endpoint_response($body, 'application/xml; charset=UTF-8');
    }

    private static function serve_wp_sitemap_endpoint() {
        if (function_exists('wp_sitemaps_get_server')) {
            $server = wp_sitemaps_get_server();
            if (is_object($server) && method_exists($server, 'render_sitemaps')) {
                // WordPress core renderer may directly print and exit.
                ob_start();
                $server->render_sitemaps();
                $rendered = trim((string) ob_get_clean());
                if ($rendered !== '') {
                    self::send_endpoint_response($rendered, 'application/xml; charset=UTF-8');
                }
            }
        }

        $body = self::build_wp_sitemap_fallback_xml();
        self::send_endpoint_response($body, 'application/xml; charset=UTF-8');
    }

    private static function build_wp_sitemap_fallback_xml() {
        $urls = array();
        $urls[home_url('/')] = gmdate('c');

        $posts = get_posts(
            array(
                'post_type' => 'any',
                'post_status' => 'publish',
                'posts_per_page' => 80,
                'orderby' => 'modified',
                'order' => 'DESC',
                'fields' => 'ids',
                'no_found_rows' => true,
            )
        );

        foreach ($posts as $post_id) {
            $permalink = get_permalink((int) $post_id);
            if (!$permalink) {
                continue;
            }
            $modified = get_post_modified_time('c', true, (int) $post_id);
            $urls[$permalink] = $modified ? $modified : gmdate('c');
        }

        $lines = array();
        $lines[] = '<?xml version="1.0" encoding="UTF-8"?>';
        $lines[] = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
        foreach ($urls as $loc => $lastmod) {
            $lines[] = '  <url>';
            $lines[] = '    <loc>' . esc_html(esc_url_raw($loc)) . '</loc>';
            $lines[] = '    <lastmod>' . esc_html($lastmod) . '</lastmod>';
            $lines[] = '  </url>';
        }
        $lines[] = '</urlset>';

        return implode("\n", $lines);
    }

    private static function build_sitemap_index_xml(array $urls) {
        $lines = array();
        $lines[] = '<?xml version="1.0" encoding="UTF-8"?>';
        $lines[] = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
        foreach ($urls as $url) {
            $lines[] = '  <sitemap>';
            $lines[] = '    <loc>' . esc_html(esc_url_raw($url)) . '</loc>';
            $lines[] = '  </sitemap>';
        }
        $lines[] = '</sitemapindex>';
        return implode("\n", $lines);
    }

    private static function should_noindex_current_request() {
        $settings = self::get_settings();
        if (empty($settings['enable_low_value_noindex'])) {
            return false;
        }

        if (is_search() || is_404()) {
            return true;
        }

        if (function_exists('is_cart') && is_cart()) {
            return true;
        }

        if (function_exists('is_checkout') && is_checkout()) {
            return true;
        }

        if (function_exists('is_account_page') && is_account_page()) {
            return true;
        }

        if (is_page()) {
            $post = get_queried_object();
            if ($post instanceof WP_Post && self::is_low_value_post($post)) {
                return true;
            }
        }

        return false;
    }

    private static function is_login_request() {
        return isset($GLOBALS['pagenow']) && $GLOBALS['pagenow'] === 'wp-login.php';
    }

    public static function output_fallback_social_meta() {
        if (is_admin() || is_feed()) {
            return;
        }

        $settings = self::get_settings();
        if (empty($settings['enable_fallback_social_meta']) || self::detect_supported_seo_plugin()) {
            return;
        }

        $context = self::get_social_context();
        if (empty($context['title']) || empty($context['url'])) {
            return;
        }

        echo "\n";
        printf("<meta property=\"og:locale\" content=\"%s\" />\n", esc_attr(str_replace('-', '_', get_locale())));
        printf("<meta property=\"og:type\" content=\"%s\" />\n", esc_attr($context['og_type']));
        printf("<meta property=\"og:title\" content=\"%s\" />\n", esc_attr($context['title']));
        printf("<meta property=\"og:description\" content=\"%s\" />\n", esc_attr($context['description']));
        printf("<meta property=\"og:url\" content=\"%s\" />\n", esc_url($context['url']));
        printf("<meta property=\"og:site_name\" content=\"%s\" />\n", esc_attr(get_bloginfo('name')));

        if (!empty($context['image'])) {
            printf("<meta property=\"og:image\" content=\"%s\" />\n", esc_url($context['image']));
        }

        printf("<meta name=\"twitter:card\" content=\"%s\" />\n", esc_attr(!empty($context['image']) ? 'summary_large_image' : 'summary'));
        printf("<meta name=\"twitter:title\" content=\"%s\" />\n", esc_attr($context['title']));
        printf("<meta name=\"twitter:description\" content=\"%s\" />\n", esc_attr($context['description']));

        if (!empty($context['image'])) {
            printf("<meta name=\"twitter:image\" content=\"%s\" />\n", esc_url($context['image']));
        }
    }

    public static function output_fallback_schema_markup() {
        if (is_admin() || is_feed()) {
            return;
        }

        $settings = self::get_settings();
        if (empty($settings['enable_fallback_schema_markup']) || self::detect_supported_seo_plugin()) {
            return;
        }

        $graph = array();
        $organization = self::build_organization_schema();
        if ($organization) {
            $graph[] = $organization;
        }

        if (is_front_page() || is_home()) {
            $website = array(
                '@type' => 'WebSite',
                '@id' => trailingslashit(home_url('/')) . '#website',
                'url' => home_url('/'),
                'name' => get_bloginfo('name'),
                'description' => self::clean_excerpt(get_bloginfo('description'), 160),
            );
            if ($organization) {
                $website['publisher'] = array('@id' => $organization['@id']);
            }
            $graph[] = $website;
        }

        if (is_singular('post')) {
            $post = get_queried_object();
            if ($post instanceof WP_Post) {
                $graph[] = self::build_article_schema($post, $organization);
            }
        }

        $graph = array_values(array_filter($graph));
        if (empty($graph)) {
            return;
        }

        $payload = array(
            '@context' => 'https://schema.org',
            '@graph' => $graph,
        );

        echo "<script type=\"application/ld+json\">" . wp_json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "</script>\n";
    }

    private static function build_organization_schema() {
        $settings = self::get_settings();
        $sameas = self::get_sameas_links($settings);
        $logo = self::get_site_logo_url($settings);

        if (empty($sameas) && empty($logo)) {
            return null;
        }

        $schema = array(
            '@type' => 'Organization',
            '@id' => trailingslashit(home_url('/')) . '#organization',
            'name' => get_bloginfo('name'),
            'url' => home_url('/'),
        );

        if (!empty($sameas)) {
            $schema['sameAs'] = $sameas;
        }

        if (!empty($logo)) {
            $schema['logo'] = array(
                '@type' => 'ImageObject',
                'url' => $logo,
            );
        }

        return $schema;
    }

    private static function build_article_schema($post, $organization) {
        if (!$post instanceof WP_Post) {
            return null;
        }

        $schema = array(
            '@type' => 'Article',
            '@id' => trailingslashit(get_permalink($post)) . '#article',
            'headline' => wp_strip_all_tags(get_the_title($post), true),
            'mainEntityOfPage' => get_permalink($post),
            'url' => get_permalink($post),
            'datePublished' => get_post_time('c', true, $post),
            'dateModified' => get_post_modified_time('c', true, $post),
            'author' => array(
                '@type' => 'Person',
                'name' => get_the_author_meta('display_name', $post->post_author),
                'url' => get_author_posts_url($post->post_author),
            ),
            'publisher' => $organization ? array('@id' => $organization['@id']) : array(
                '@type' => 'Organization',
                'name' => get_bloginfo('name'),
                'url' => home_url('/'),
            ),
        );

        $description = get_the_excerpt($post);
        if (!$description) {
            $description = $post->post_content;
        }
        $schema['description'] = self::clean_excerpt($description, 180);

        $image = self::get_post_image_url($post);
        if ($image) {
            $schema['image'] = array($image);
        }

        return $schema;
    }

    private static function get_social_context() {
        $context = array(
            'title' => '',
            'description' => '',
            'url' => '',
            'image' => '',
            'og_type' => 'website',
        );

        if (is_front_page() || is_home()) {
            $context['title'] = get_bloginfo('name');
            $context['description'] = self::clean_excerpt(get_bloginfo('description') ?: get_bloginfo('name'), 180);
            $context['url'] = home_url('/');
            $context['image'] = self::get_site_logo_url();
            return $context;
        }

        if (is_singular()) {
            $post = get_queried_object();
            if ($post instanceof WP_Post) {
                $description = get_the_excerpt($post);
                if (!$description) {
                    $description = $post->post_content;
                }
                $context['title'] = wp_strip_all_tags(get_the_title($post), true);
                $context['description'] = self::clean_excerpt($description, 180);
                $context['url'] = get_permalink($post);
                $context['image'] = self::get_post_image_url($post);
                $context['og_type'] = $post->post_type === 'post' ? 'article' : 'website';
            }
        }

        return $context;
    }

    private static function get_googlebot_user_agent() {
        return 'Mozilla/5.0 (Linux; Android 12; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)';
    }

    private static function extract_link_tag_href($html, $rel) {
        $rel = strtolower(trim((string) $rel));
        if ($rel === '') {
            return '';
        }

        if (class_exists('DOMDocument')) {
            $dom = new DOMDocument();
            libxml_use_internal_errors(true);
            $loaded = $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html);
            libxml_clear_errors();
            if ($loaded) {
                $links = $dom->getElementsByTagName('link');
                foreach ($links as $link) {
                    if (!$link instanceof DOMElement) {
                        continue;
                    }
                    if (strtolower(trim($link->getAttribute('rel'))) === $rel) {
                        return trim($link->getAttribute('href'));
                    }
                }
            }
        }

        if (preg_match_all('/<link\b[^>]*rel=["\']([^"\']+)["\'][^>]*href=["\']([^"\']+)["\'][^>]*>/i', $html, $matches, PREG_SET_ORDER)) {
            foreach ($matches as $match) {
                if (strtolower(trim($match[1])) === $rel) {
                    return trim($match[2]);
                }
            }
        }

        return '';
    }

    private static function urls_equivalent($a, $b) {
        $a = self::normalize_comparable_url($a);
        $b = self::normalize_comparable_url($b);
        return $a !== '' && $a === $b;
    }

    private static function normalize_comparable_url($url) {
        $url = trim((string) $url);
        if ($url === '') {
            return '';
        }

        if (strpos($url, '/') === 0) {
            $url = home_url(self::normalize_path($url));
        }

        $parts = wp_parse_url($url);
        if (!is_array($parts) || empty($parts['host'])) {
            return '';
        }

        $scheme = !empty($parts['scheme']) ? strtolower($parts['scheme']) : 'https';
        $host = strtolower($parts['host']);
        $path = isset($parts['path']) ? self::normalize_path($parts['path']) : '/';
        $query = '';

        if (!empty($parts['query'])) {
            parse_str($parts['query'], $query_args);
            ksort($query_args);
            $query = http_build_query($query_args);
        }

        return $scheme . '://' . $host . $path . ($query !== '' ? '?' . $query : '');
    }

    private static function compare_fetch_variants($label, $default_response, $bot_response) {
        if (!empty($default_response['error']) || !empty($bot_response['error'])) {
            return array(
                'status' => 'fail',
                'detail' => $label . '：普通请求或 Googlebot 请求失败。',
                'problem' => $label . ' 请求失败，无法验证 UA 一致性。',
            );
        }

        $status_default = (int) (isset($default_response['status_code']) ? $default_response['status_code'] : 0);
        $status_bot = (int) (isset($bot_response['status_code']) ? $bot_response['status_code'] : 0);
        $type_default = isset($default_response['content_type']) ? (string) $default_response['content_type'] : '';
        $type_bot = isset($bot_response['content_type']) ? (string) $bot_response['content_type'] : '';
        $title_default = self::extract_html_title(isset($default_response['body']) ? $default_response['body'] : '');
        $title_bot = self::extract_html_title(isset($bot_response['body']) ? $bot_response['body'] : '');
        $len_default = self::get_html_text_length(isset($default_response['body']) ? $default_response['body'] : '');
        $len_bot = self::get_html_text_length(isset($bot_response['body']) ? $bot_response['body'] : '');

        $detail = $label . '：普通 ' . $status_default . ' / Googlebot ' . $status_bot
            . '；Title=' . ($title_default !== '' ? $title_default : '无')
            . ' / ' . ($title_bot !== '' ? $title_bot : '无')
            . '；正文长度=' . $len_default . ' / ' . $len_bot;

        if ($status_default !== $status_bot || $type_default !== $type_bot) {
            return array(
                'status' => 'fail',
                'detail' => $detail,
                'problem' => $label . ' 的状态码或 Content-Type 在 Googlebot UA 下发生变化。',
            );
        }

        if ($title_default !== $title_bot) {
            return array(
                'status' => 'warn',
                'detail' => $detail,
                'problem' => $label . ' 的 HTML title 在 Googlebot UA 下不同。',
            );
        }

        $base = max(1, $len_default);
        $diff_ratio = abs($len_default - $len_bot) / $base;
        if ($diff_ratio > 0.35) {
            return array(
                'status' => 'warn',
                'detail' => $detail,
                'problem' => $label . ' 的主体内容长度差异较大，可能存在缓存或防护分流。',
            );
        }

        return array(
            'status' => 'pass',
            'detail' => $detail,
            'problem' => '',
        );
    }

    private static function extract_html_title($html) {
        if (preg_match('/<title[^>]*>(.*?)<\/title>/is', (string) $html, $matches)) {
            return trim(wp_strip_all_tags(html_entity_decode($matches[1], ENT_QUOTES, 'UTF-8'), true));
        }
        return '';
    }

    private static function get_html_text_length($html) {
        $text = html_entity_decode((string) $html, ENT_QUOTES, 'UTF-8');
        $text = preg_replace('/<script\b[^>]*>.*?<\/script>/is', ' ', $text);
        $text = preg_replace('/<style\b[^>]*>.*?<\/style>/is', ' ', $text);
        $text = wp_strip_all_tags($text, true);
        $text = preg_replace('/\s+/u', ' ', $text);
        return function_exists('mb_strlen') ? mb_strlen(trim($text), 'UTF-8') : strlen(trim($text));
    }

    private static function get_llms_href_path() {
        $href = wp_parse_url(home_url('/llms.txt'), PHP_URL_PATH);
        return $href ? $href : '/llms.txt';
    }

    private static function get_post_image_url($post) {
        if (!$post instanceof WP_Post) {
            return '';
        }

        $thumb = get_the_post_thumbnail_url($post, 'full');
        if ($thumb) {
            return $thumb;
        }

        return self::get_site_logo_url();
    }

    private static function get_site_logo_url($settings = null) {
        if (!is_array($settings)) {
            $settings = self::get_settings();
        }

        if (!empty($settings['organization_logo_url'])) {
            return $settings['organization_logo_url'];
        }

        $site_icon_id = get_option('site_icon');
        if ($site_icon_id) {
            $icon = wp_get_attachment_image_url($site_icon_id, 'full');
            if ($icon) {
                return $icon;
            }
        }

        return '';
    }

    private static function get_active_seo_plugins() {
        $plugins = array(
            'Yoast SEO' => defined('WPSEO_VERSION') || class_exists('WPSEO_Frontend'),
            'Rank Math' => defined('RANK_MATH_VERSION') || class_exists('RankMath'),
            'All in One SEO' => defined('AIOSEO_VERSION') || class_exists('AIOSEO\\Plugin\\AIOSEO'),
            'SEOPress' => defined('SEOPRESS_VERSION') || class_exists('SEOPress\\SEOPress'),
            'The SEO Framework' => class_exists('The_SEO_Framework\\Load') || defined('THE_SEO_FRAMEWORK_VERSION'),
            'Jetpack' => defined('JETPACK__VERSION'),
        );

        $active = array();
        foreach ($plugins as $name => $enabled) {
            if ($enabled) {
                $active[] = $name;
            }
        }

        return $active;
    }

    private static function get_active_multilingual_plugins() {
        $plugins = array();

        if (defined('POLYLANG_VERSION') || function_exists('pll_current_language')) {
            $plugins[] = 'Polylang';
        }

        if (defined('ICL_SITEPRESS_VERSION') || class_exists('SitePress') || function_exists('icl_object_id')) {
            $plugins[] = 'WPML';
        }

        return $plugins;
    }

    private static function get_integration_context() {
        $front_page_mode = get_option('show_on_front') === 'page' ? 'static' : 'posts';
        $front_page_id = absint(get_option('page_on_front'));
        $posts_page_id = absint(get_option('page_for_posts'));

        return array(
            'seo_plugins' => self::get_active_seo_plugins(),
            'woocommerce' => class_exists('WooCommerce'),
            'multilingual' => self::get_active_multilingual_plugins(),
            'front_page_mode' => $front_page_mode,
            'front_page_mode_label' => $front_page_mode === 'static' ? '静态首页 + 独立博客页' : '首页即最新文章',
            'front_page_id' => $front_page_id,
            'front_page_title' => $front_page_id ? get_the_title($front_page_id) : '',
            'posts_page_id' => $posts_page_id,
            'posts_page_title' => $posts_page_id ? get_the_title($posts_page_id) : '',
        );
    }

    private static function detect_supported_seo_plugin() {
        $active = self::get_active_seo_plugins();
        return !empty($active) ? $active[0] : '';
    }
}

GEO_LLMS_Auto_Regenerator::init();
register_activation_hook(__FILE__, array('GEO_LLMS_Auto_Regenerator', 'on_activate'));
register_deactivation_hook(__FILE__, array('GEO_LLMS_Auto_Regenerator', 'on_deactivate'));
