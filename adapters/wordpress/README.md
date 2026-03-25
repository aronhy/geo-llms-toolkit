# GEO LLMS Auto Regenerator（WordPress 版本）

这个版本用于 WordPress 后台一键运行 GEO 工作流，不需要命令行。

## 1. 功能概览

- 内容发布/更新自动重建 `llms.txt` 和 `llms-full.txt`
- GEO 扫描（robots/sitemap/llms + SEO/GEO 信号）
- 安全修复（预览、应用、回滚）
- CLI 迁移工作台（后台按钮版）：
- `Monitor`
- `Outreach plan/run/verify`
- `Index discover/track/submit/audit/report`
- 定时扫描、历史趋势、通知（邮件/Webhook）
- 缓存联动（Cloudflare + 常见 WP 缓存插件）

## 2. 部署安装

### 方式 A：从仓库打包 ZIP 安装（推荐）

```bash
cd /path/to/geo-llms-toolkit
./scripts/build-wordpress-zip.sh
```

生成文件：

- `dist/geo-llms-auto-regenerator-<version>.zip`

然后在 WordPress 后台：

1. `插件 -> 安装插件 -> 上传插件`
2. 上传 ZIP
3. 激活插件 `GEO LLMS Auto Regenerator`

### 方式 B：源码目录安装

把 `adapters/wordpress` 拷到：

- `/wp-content/plugins/geo-llms-auto-regenerator`

然后在后台启用。

## 3. 首次配置（建议顺序）

后台路径：

- `设置 -> GEO LLMS Auto`

首次建议依次操作：

1. 保存一次设置（确保计划任务与选项落库）
2. 点击 `立即重建 llms 文件`
3. 点击 `立即扫描 GEO`
4. 点击 `预览安全修复`，确认后再点 `一键应用安全修复`

## 4. CLI 迁移工作台（后台用法）

在同一个页面有 `CLI 迁移工作台`：

1. `运行 Monitor`
2. `生成 Outreach 计划`
3. `执行 Outreach`
4. `验证 Outreach`
5. `Index Discover`
6. `Index Track`
7. `Index Submit`
8. `Index Audit`
9. `Index Report`

## 5. 关键配置项

### Monitor

- 关键词列表（每行一个）
- 竞品域名（可空）
- SERP 深度、权重、最大关键词数

### Outreach

- `pitch URL`
- provider：`dry-run / webhook / command`
- webhook URL/token 或 command template
- 冷却天数、follow-up 天数

### Index

- URL 池上限
- 跟踪深度
- 提交 provider：`dry-run / google-indexing / webhook / command`
- 提交状态过滤（默认 `not_indexed,unknown`）
- 审计薄内容阈值、报告窗口天数

## 6. 验证清单

部署后至少跑一次：

1. `运行 Monitor` 成功返回关键词/竞品/动作数量
2. `生成 Outreach 计划` 出现 prospects
3. `Index Discover + Index Track` 生成 URL 和收录状态
4. `Index Audit` 有问题分级（P0/P1/P2）
5. `Index Report` 生成趋势数据

## 7. 详细环境配置

- 宝塔 + Nginx + Cloudflare 详细指南：
- `../../docs/wordpress-detailed-setup.md`

## 8. 注意事项

- 根目录需可写，否则 llms 文件无法生成
- 若站点有强缓存，建议开启缓存联动
- `google-indexing` 需要有效 token，先用 `dry-run` 验证流程
