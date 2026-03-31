# geo-llms-toolkit

开源 GEO 工具集，提供两条交付路径：

- Standalone CLI（任意站点：静态站 / Shopify / 自建 CMS）
- WordPress 插件（后台按钮化运维）

## 当前版本

- Standalone CLI: `0.16.0`
- WordPress Plugin (`GEO LLMS Auto Regenerator`): `1.9.0`

`1.9.0` 已补齐 WordPress 对 CLI 关键参数的对齐项（brand-token / exclude-domain / enrich-contacts / apify-allow-fallback-first）。

## 能力地图

- GEO 扫描：robots / sitemap / llms 端点 + SEO/GEO 信号检查
- LLMS 生成：`llms.txt` / `llms-full.txt`
- 竞品监控：关键词级监控、评分、优先级动作建议
- 外联流程：plan / run / verify / status / update
- 收录流程：discover / track / submit / audit / report
- WordPress 安全修复：预览、应用、回滚、定时任务、通知、缓存联动

## 快速选择

| 版本 | 适用场景 | 文档 |
| --- | --- | --- |
| Standalone CLI | 你要接任意站点并用命令行自动化 | [standalone/README.md](./standalone/README.md) |
| WordPress 插件 | 你要在 WP 后台直接操作 | [adapters/wordpress/README.md](./adapters/wordpress/README.md) |

## Standalone CLI 快速开始

```bash
git clone https://github.com/aronhy/geo-llms-toolkit.git
cd geo-llms-toolkit
chmod +x geo
./geo --help
```

最短实战链路：

```bash
./geo scan aronhouyu.com --platform-profile auto --rules-file ./.geo-rules.json
./geo llms aronhouyu.com --output-dir ./output
./geo adapter-check aronhouyu.com --format markdown
./geo monitor aronhouyu.com --keywords-file ./examples/keywords.txt --discover-competitors --output ./output/monitor.json --format json
# 网络受限时可切换或回退 SERP provider
./geo monitor aronhouyu.com --keywords-file ./examples/keywords.txt --serp-provider auto --serp-retries 2 --serp-backoff-ms 500
# 自动过滤过泛关键词（可选）
./geo monitor aronhouyu.com --keywords-file ./examples/keywords.txt --rules-file ./.geo-rules.json --drop-low-specificity-keywords
./geo outreach plan --monitor-report ./output/monitor.json --pitch-url https://aronhouyu.com --output-dir ./output
./geo index discover aronhouyu.com --output ./output/index-discover.json --format json
./geo index track aronhouyu.com --discover-report ./output/index-discover.json --output ./output/index-track.json --format json
./geo index audit aronhouyu.com --from-track-report ./output/index-track.json --output ./output/index-audit.md
./geo index report aronhouyu.com --history-dir ./.geo-history/index --days 30 --output ./output/index-report.md
```

CLI 命令组：

- `scan`, `llms`, `adapter-check`, `all`
- `monitor`, `monitor-diff`
- `outreach (plan/run/status/verify/update)`
- `index (discover/track/submit/audit/report)`

`monitor` 关键参数补充：

- `--serp-provider auto|bing|duckduckgo-lite`（默认 `auto`，先 Bing 后 DuckDuckGo Lite）
- `--serp-retries`（默认 `1`）
- `--serp-backoff-ms`（默认 `350`）
- `--rules-file`（可让 `keyword_quality` 从 `.geo-rules.json` 生效）
- `--drop-low-specificity-keywords`（自动过滤 single-token / all-generic 过泛词）
- `--keep-low-specificity-keywords`（覆盖规则文件，强制不过滤）

`monitor` 结果里的 `diagnostics` 新增：

- `keyword_load_stats`（raw/kept/duplicate_skipped/low_specificity 等）
- `keyword_low_specificity_samples`（示例关键词 + 原因）

`scan` 关键参数补充：

- `--rules-file`（默认会自动读取根目录 `.geo-rules.json`）
- `--platform-profile auto|wordpress|shopify|webflow|ghost|custom`
- `scan` 输出新增：`meta.platform`、`meta.platform_confidence`、`meta.discovery`、`checks[*].applicability`

## WordPress 插件快速开始

在仓库根目录打包安装包：

```bash
./scripts/build-wordpress-zip.sh
```

生成：

- `dist/geo-llms-auto-regenerator-1.9.0.zip`

安装与初始化：

1. WordPress 后台 -> `插件 -> 安装插件 -> 上传插件`
2. 上传 ZIP 并启用 `GEO LLMS Auto Regenerator`
3. 进入 `设置 -> GEO LLMS Auto`
4. 先执行一次：
- `立即重建 llms 文件`
- `立即扫描 GEO`

插件工作台支持：

- Monitor / Monitor diff
- Outreach plan/run/verify/status/update
- Index discover/track/submit/audit/report
- 模块导出（monitor / outreach / index，markdown/json/csv）

## CLI -> WordPress 对齐状态

对齐矩阵见：

- [docs/cli-wordpress-parity.md](./docs/cli-wordpress-parity.md)

重点说明：

- WordPress 已覆盖 CLI 主流程与关键运营参数
- 文件路径型参数（如 `--history-dir` / `--campaign-file`）在 WP 中改为数据库状态管理

## 文档导航

- CLI 文档：[standalone/README.md](./standalone/README.md)
- WordPress 文档：[adapters/wordpress/README.md](./adapters/wordpress/README.md)
- WordPress 全参数配置教程：[docs/wordpress-config-tutorial.md](./docs/wordpress-config-tutorial.md)
- WordPress 详细环境配置（宝塔/Nginx/Cloudflare）：[docs/wordpress-detailed-setup.md](./docs/wordpress-detailed-setup.md)
- Outreach 适配说明：[docs/backlink-outreach-js-adapter.md](./docs/backlink-outreach-js-adapter.md)
- 迁移计划：[docs/migration-plan.md](./docs/migration-plan.md)
- 适配器合同：[core/docs/adapter-contract.md](./core/docs/adapter-contract.md)
- 本期 PRD：[PRD-universal-site-adaptation.md](./PRD-universal-site-adaptation.md)

## Known Limitations

- 平台识别是启发式规则，极少数站点可能低置信度回退到 `custom`；可用 `--platform-profile` 强制指定。
- `llms.txt` / `llms-full.txt` 采用强校验（非 `200` 视为失败）。对未部署 LLMS 文件的网站会产生 `FAIL`。
- Shopify / Webflow / Ghost 目前在 CLI 仅提供只读适配（扫描/诊断/报告），不做自动写入修复。

## 开发命令

```bash
# WordPress 插件语法检查
php -l adapters/wordpress/geo-llms-auto-regenerator.php
php -l adapters/wordpress/uninstall.php
php -l adapters/wordpress/languages/index.php

# CLI 语法检查
python3 -m py_compile standalone/geo_toolkit.py core/python/adapter_contract.py

# 跨平台扫描回归（非WP不应因 wp-sitemap.xml 硬失败）
./scripts/check-universal-scan-regression.sh

# 重新打包 WordPress ZIP
./scripts/build-wordpress-zip.sh

# CLI 帮助
./geo --help
```

## 开源信息

- License: `GPL-2.0-or-later`
- Issues / PR 欢迎提交

## Follow

- X: [https://x.com/aronhouyu](https://x.com/aronhouyu)
- YouTube: [https://www.youtube.com/@aronhouyu1024](https://www.youtube.com/@aronhouyu1024)
