# geo-llms-toolkit

开源 GEO 工具集，包含两个可独立使用的版本：

- **Standalone CLI（非 WordPress）**
- **WordPress 插件（GEO LLMS Auto Regenerator）**

## 版本选择

| 版本 | 适用场景 | 入口 |
| --- | --- | --- |
| Standalone CLI | 任意网站（静态站/Shopify/自建 CMS） | [standalone/README.md](./standalone/README.md) |
| WordPress 插件 | 希望在后台按钮化运行 | [adapters/wordpress/README.md](./adapters/wordpress/README.md) |

## 1) Standalone CLI 快速部署

```bash
git clone https://github.com/aronhy/geo-llms-toolkit.git
cd geo-llms-toolkit
chmod +x geo
./geo --help
```

最短闭环：

```bash
./geo scan aronhouyu.com
./geo llms aronhouyu.com --output-dir ./output
./geo monitor aronhouyu.com --keywords-file ./examples/keywords.txt --discover-competitors --output ./output/monitor.json --format json
./geo index discover aronhouyu.com --output ./output/index-discover.json --format json
./geo index track aronhouyu.com --discover-report ./output/index-discover.json --output ./output/index-track.json --format json
./geo index audit aronhouyu.com --from-track-report ./output/index-track.json --output ./output/index-audit.md
./geo index report aronhouyu.com --history-dir ./.geo-history/index --days 30 --output ./output/index-report.md
```

## 2) WordPress 插件快速部署

在仓库根目录打包：

```bash
./scripts/build-wordpress-zip.sh
```

得到：

- `dist/geo-llms-auto-regenerator-1.7.0.zip`

安装：

1. WordPress 后台 -> `插件 -> 安装插件 -> 上传插件`
2. 上传 ZIP 并启用 `GEO LLMS Auto Regenerator`
3. 进入 `设置 -> GEO LLMS Auto`
4. 先点一次：
- `立即重建 llms 文件`
- `立即扫描 GEO`

插件已内置 CLI 迁移工作台按钮：

- `Monitor`
- `Outreach plan/run/verify`
- `Index discover/track/submit/audit/report`

## 3) 核心能力

- 站点 GEO 诊断：端点、抓取、schema、noindex、软 404 等
- LLMS 生成：`llms.txt` / `llms-full.txt`
- 竞品监控：关键词级别评分、优先级动作建议
- 外联闭环：计划、执行、状态、回访
- 收录闭环：发现、跟踪、提交、审计、周报
- WordPress 安全修复闭环：预览、应用、回滚、定时任务、通知、缓存联动

## 4) 文档导航

- CLI 使用与部署：[standalone/README.md](./standalone/README.md)
- WordPress 使用与部署：[adapters/wordpress/README.md](./adapters/wordpress/README.md)
- WordPress 详细环境配置（宝塔/Nginx/Cloudflare）：[docs/wordpress-detailed-setup.md](./docs/wordpress-detailed-setup.md)
- 外联适配说明：[docs/backlink-outreach-js-adapter.md](./docs/backlink-outreach-js-adapter.md)

## 5) 开发命令

```bash
# WordPress 插件语法检查
php -l adapters/wordpress/geo-llms-auto-regenerator.php
php -l adapters/wordpress/uninstall.php
php -l adapters/wordpress/languages/index.php

# 重新打包 WordPress ZIP
./scripts/build-wordpress-zip.sh

# CLI 帮助
python3 standalone/geo_toolkit.py --help
```

## 6) 开源信息

- License: `GPL-2.0-or-later`
- Issues / PR 欢迎提交

## Follow

- X: [https://x.com/aronhouyu](https://x.com/aronhouyu)
- YouTube: [https://www.youtube.com/@aronhouyu1024](https://www.youtube.com/@aronhouyu1024)
