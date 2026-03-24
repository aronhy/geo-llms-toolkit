# WordPress Adapter

This adapter is currently the primary production implementation in this repository.

## What is included

- plugin main file: `geo-llms-auto-regenerator.php`
- plugin readme: `readme.txt`
- uninstall cleanup: `uninstall.php`

## Install

1. Zip the folder as a WordPress plugin package.
2. Install in WordPress admin (`Plugins -> Add New -> Upload Plugin`).
3. Activate and configure under `Settings -> GEO LLMS Auto`.

## Notes

- This adapter intentionally keeps WordPress-specific hooks and admin UI.
- Platform-neutral logic will be extracted to `core` in future releases.
