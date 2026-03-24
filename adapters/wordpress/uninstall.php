<?php

if (!defined('WP_UNINSTALL_PLUGIN')) {
    exit;
}

$settings = get_option('geo_llms_autogen_settings', array());
if (empty($settings['cleanup_on_uninstall'])) {
    return;
}

$option_keys = array(
    'geo_llms_autogen_last_result',
    'geo_llms_autogen_last_scan',
    'geo_llms_autogen_scan_history',
    'geo_llms_autogen_settings',
    'geo_llms_autogen_notice',
    'geo_llms_autogen_fix_preview',
    'geo_llms_autogen_settings_backup',
    'geo_llms_autogen_plugin_version',
    'geo_llms_autogen_logs',
    'geo_llms_autogen_cache_queue',
);

foreach ($option_keys as $option_key) {
    delete_option($option_key);
    delete_site_option($option_key);
}

global $wpdb;
$meta_keys = array(
    '_geo_llms_summary',
    '_geo_llms_pin',
    '_geo_llms_exclude',
);

foreach ($meta_keys as $meta_key) {
    $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->postmeta} WHERE meta_key = %s", $meta_key));
}
