# WordPress 配置教程（全参数版）

适用版本：

- 插件：`GEO LLMS Auto Regenerator 1.9.0`
- 后台入口：`设置 -> GEO LLMS Auto`

本文目标：把“装好插件后到底怎么配”一次讲清楚，包含参数解释、推荐值、操作顺序和常见场景模板。

## 1. 先做这 7 步（上线最短路径）

1. 安装并启用插件。
2. 打开 `设置 -> GEO LLMS Auto`，先点一次“保存设置”。
3. 点击“立即重建 llms 文件”。
4. 点击“立即扫描 GEO”。
5. 点击“预览安全修复”并确认结果。
6. 点击“一键应用安全修复”。
7. 用 `curl -I` 检查以下地址都为 `200`：
- `/robots.txt`
- `/sitemap.xml`
- `/sitemap_index.xml`
- `/wp-sitemap.xml`
- `/llms.txt`
- `/llms-full.txt`

## 2. 参数总览（按后台分区）

## 2.1 产品基础设施

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 后台权限 | `management_capability` | `manage_options` | `manage_options` | 控制谁可操作插件。 |
| 启用错误日志 | `logging_enabled` | `1` | `1` | 记录关键流程日志。 |
| 卸载时清理 | `cleanup_on_uninstall` | `0` | `0`（生产） | 开启后卸载插件会删除设置/历史/日志。 |

## 2.2 LLMS 规则中心

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 纳入文章类型 | `included_post_types` | `post` | `post,page`（内容站） | 决定哪些 post type 参与 llms 生成。 |
| 纳入分类项 | `included_term_keys` | 空 | 按业务选择 | 限制进入 llms 的 taxonomy 范围。 |
| 手动 Pin | `pinned_refs` | 空 | 核心页手动加 | 支持 ID/URL/path/slug，优先进入 Featured。 |
| 全局排除 | `excluded_refs` | 空 | 排除低价值页 | 支持 ID/URL/path/slug。 |
| 低价值页排除 | `exclude_low_value_from_llms` | `1` | `1` | 自动过滤登录/示例/低价值条目。 |
| 首页声明 llms link | `enable_llms_link_tag` | `1` | `1` | 在首页 head 输出 `<link rel="llms" href="/llms.txt">`。 |

## 2.3 安全修复

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 安全模式 | `safe_fix_mode` | `strict` | `strict` | `strict` 仅低风险修复，不改 H1/H2/CSS/UI。 |
| 低价值 noindex | `enable_low_value_noindex` | `0` | `1` | 给登录/找回密码/示例页加 noindex。 |
| WP 端点修复 | `enable_wp_endpoint_fix` | `0` | `1` | 修复 robots/sitemap 端点被错误重写。 |
| fallback OG/Twitter | `enable_fallback_social_meta` | `0` | `0` 或 `1` | 未检测到 SEO 插件时再开。 |
| fallback schema | `enable_fallback_schema_markup` | `0` | `0` 或 `1` | 未检测到 SEO 插件时再开。 |
| 组织 Logo URL | `organization_logo_url` | 空 | 品牌 logo | 用于 Organization schema。 |
| sameAs 链接 | `organization_sameas` | 空 | 填全品牌社媒 | 每行一个 URL。 |

## 2.4 缓存联动

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 启用缓存联动 | `cache_purge_enabled` | `0` | `1` | llms 重建后自动清缓存，避免前后台不一致。 |
| 本地缓存清理 | `cache_purge_local_enabled` | `1` | `1` | 自动调用常见 WP 缓存插件 purge。 |
| Cloudflare 缓存清理 | `cache_purge_cloudflare_enabled` | `0` | 用 CF 就开 | 需要 Zone ID + API Token。 |
| Cloudflare Zone ID | `cache_purge_cloudflare_zone_id` | 空 | 实际值 | Cloudflare 配置项。 |
| Cloudflare API Token | `cache_purge_cloudflare_api_token` | 空 | 实际值 | 需有 cache purge 权限。 |
| 清理模式 | `cache_purge_cloudflare_mode` | `selected` | `selected` | `selected` 比 `everything` 更稳。 |
| 额外清理 URL | `cache_purge_additional_urls` | 空 | 栏目页/专题页 | 每行一个 URL 或路径。 |

## 2.5 定时扫描与 Agent

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 启用定时扫描 | `scheduled_scan_enabled` | `0` | `1` | 开启后自动执行巡检。 |
| 扫描频率 | `scheduled_scan_frequency` | `weekly` | `weekly` | 可选 `daily`/`weekly`。 |
| 每周扫描日 | `scheduled_scan_weekday` | `mon` | `mon` | 仅 weekly 模式生效。 |
| 扫描小时 | `scheduled_scan_hour` | `9` | 低峰时段 | 0-23。 |
| GEO Agent 闭环 | `agent_mode_enabled` | `0` | `0`（先观察） | 开启后会扫描->修复->复扫->必要时回滚。 |
| 自动安全修复 | `auto_safe_fix_enabled` | `0` | `1` | 扫描后自动修复安全项。 |
| 手动扫描自动修复 | `auto_safe_fix_on_manual_scan` | `0` | `0` | 先人工确认更稳。 |
| 历史保留条数 | `scan_history_limit` | `20` | `20~50` | 影响历史趋势长度。 |

## 2.6 通知

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| Fail 通知 | `notify_on_fail` | `1` | `1` | 出现失败项时通知。 |
| Warn 通知 | `notify_on_warn` | `1` | `1` | 仅 Warn 也通知。 |
| 手动扫描也通知 | `notify_on_manual_scan` | `0` | `0` | 一般不用。 |
| 邮件通知开关 | `notify_email_enabled` | `0` | `1` | 与通知邮箱配合。 |
| 通知邮箱 | `notification_email` | 管理员邮箱 | 团队邮箱 | 接收扫描摘要。 |
| Webhook 通知开关 | `notify_webhook_enabled` | `0` | 按需 | 对接 Slack/飞书/自建 webhook。 |
| Webhook URL | `notification_webhook_url` | 空 | 按需 | 接收通知事件。 |
| 邮件标题模板 | `notification_email_subject_template` | 内置默认模板 | 可自定义 | 支持占位符。 |
| 邮件正文模板 | `notification_email_body_template` | 内置默认模板 | 可自定义 | 支持占位符。 |
| Webhook 模板 | `notification_webhook_template` | 内置默认模板 | 可自定义 | 支持占位符。 |

模板可用占位符：

- `{{site_name}}`, `{{site_url}}`, `{{scan_time}}`, `{{trigger}}`, `{{overall_status}}`
- `{{summary}}`, `{{trend}}`, `{{issues}}`, `{{recommendations}}`
- 以及对应 `*_json` 版本字段

## 2.7 Monitor 参数

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 关键词列表 | `monitor_keywords` | 空 | 手工填核心关键词 | 每行一个，可带分组。 |
| 竞品域名 | `monitor_competitors` | 空 | 3~10 个 | 手工指定竞品池。 |
| 品牌词 Token | `monitor_brand_tokens` | 空 | 品牌词都填上 | 等价 CLI `--brand-token`。 |
| 自动发现竞品 | `monitor_discover_competitors` | `1` | `1` | 从 SERP 补充竞品。 |
| SERP 深度 | `monitor_serp_depth` | `10` | `10` | 5~50。 |
| 最大关键词数 | `monitor_max_keywords` | `80` | `50~120` | 控制扫描开销。 |
| 自动发现上限 | `monitor_max_discovered` | `8` | `8` | 新发现竞品数量上限。 |
| 权重：关键词重叠 | `monitor_weight_keyword_overlap` | `45` | `45` | 评分权重。 |
| 权重：同页共现 | `monitor_weight_serp_coappear` | `35` | `35` | 评分权重。 |
| 权重：排名压力 | `monitor_weight_rank_pressure` | `20` | `20` | 评分权重。 |

## 2.8 Outreach 参数

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| 推广 URL | `outreach_pitch_url` | 空 | 必填 | 外联目标页。 |
| 站点名称 | `outreach_site_name` | 站点名 | 品牌名 | 用于邮件模板署名。 |
| Offer | `outreach_offer` | `Resource inclusion request` | 按业务写 | 外联价值点。 |
| Provider | `outreach_provider` | `dry-run` | 先 `dry-run` | `dry-run/webhook/command/apify`。 |
| Webhook URL | `outreach_webhook_url` | 空 | provider=webhook 必填 | 外联执行入口。 |
| Webhook Token | `outreach_webhook_token` | 空 | 按需 | Bearer token。 |
| Command 模板 | `outreach_command_template` | 空 | provider=command 必填 | 支持 `{domain_q}` 等变量。 |
| 最大 prospects | `outreach_max_prospects` | `30` | `20~50` | 一次计划包含条数。 |
| 最低评分 | `outreach_min_prospect_score` | `8` | `8~15` | 提高会更保守。 |
| 最低机会数 | `outreach_min_opportunities` | `1` | `1~2` | 过滤机会太少域名。 |
| 排除域名 | `outreach_exclude_domains` | 空 | 黑名单都填 | 等价 CLI `--exclude-domain`。 |
| 联系人探测 | `outreach_enrich_contacts` | `0` | `1`（要跑外联） | 等价 CLI `--enrich-contacts`。 |
| 冷却天数 | `outreach_cooldown_days` | `21` | `21` | 防止重复触达。 |
| followup 天数 | `outreach_followup_days` | `7` | `7` | 到期进入 followup_due。 |
| 包含冷却域名 | `outreach_include_existing` | `0` | `0` | 等价 CLI `--include-existing`。 |
| 执行 followup_due | `outreach_run_followup_due` | `0` | `1`（二触达时） | 等价 CLI `--run-followup-due`。 |
| Apify Token | `outreach_apify_token` | 空 | provider=apify 必填 | Apify 鉴权。 |
| Apify Actor | `outreach_apify_actor_id` | `daniil.poletaev/backlink-building-agent` | 按需 | 执行 actor。 |
| Apify fallback-first | `outreach_apify_allow_fallback_first` | `0` | 按需 | 等价 CLI `--apify-allow-fallback-first`。 |

## 2.9 Index 参数

| 配置项 | key | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- | --- |
| URL 池上限 | `index_max_urls` | `220` | `220~500` | discover/track 的 URL 数。 |
| SERP 深度 | `index_search_depth` | `8` | `8` | 用于 track 的搜索验证。 |
| strict search | `index_strict_search` | `0` | `1` | 搜索无精确匹配时判 `not_indexed`。 |
| 长期未收录阈值 | `index_long_unindexed_days` | `14` | `14` | track 的 long_unindexed 判断。 |
| 掉索引告警开关 | `index_alert_on_drop` | `0` | `1` | track 发现掉索引时触发告警。 |
| 告警 Webhook URL | `index_alert_webhook_url` | 空 | 按需 | 接收掉索引/长期未收录告警。 |
| 告警 Webhook Token | `index_alert_webhook_token` | 空 | 按需 | 告警鉴权。 |
| 提交 Provider | `index_submit_provider` | `webhook` | 先 `dry-run` | `dry-run/google-indexing/webhook/command`。 |
| 通知类型 | `index_notification_type` | `URL_UPDATED` | `URL_UPDATED` | 也可 `URL_DELETED`。 |
| 状态过滤 | `index_submit_status_filter` | `not_indexed,unknown` | 默认即可 | submit 从 track 里筛哪些 URL。 |
| Google Token | `index_google_token` | 空 | provider=google-indexing 必填 | Indexing API token。 |
| 允许不支持类型 | `index_allow_unsupported_google_types` | `0` | `0` | 避免误推不支持 URL 类型。 |
| Submit Webhook URL | `index_webhook_url` | 空 | provider=webhook 必填 | 提交执行地址。 |
| Submit Webhook Token | `index_webhook_token` | 空 | 按需 | 提交鉴权。 |
| Submit Command 模板 | `index_command_template` | 空 | provider=command 必填 | 自定义提交命令。 |
| 薄内容阈值 | `index_thin_threshold_chars` | `380` | `350~500` | audit 的 thin_content 判断。 |
| 报告窗口 | `index_report_days` | `30` | `30` | 周报窗口天数。 |

## 3. 推荐配置模板（可直接照抄）

## 3.1 内容博客站（推荐）

- `safe_fix_mode=strict`
- `exclude_low_value_from_llms=1`
- `enable_low_value_noindex=1`
- `enable_wp_endpoint_fix=1`
- `cache_purge_enabled=1`
- `scheduled_scan_enabled=1` + `weekly`
- `notify_on_fail=1`, `notify_on_warn=1`
- `monitor_discover_competitors=1`
- `outreach_provider=dry-run`（先跑计划）
- `index_strict_search=1`, `index_alert_on_drop=1`

## 3.2 先只做检测，不自动改

- 关闭 `auto_safe_fix_enabled`
- 关闭 `agent_mode_enabled`
- 开启通知与导出，先观察 1~2 周

## 3.3 要做自动化闭环

- 开启 `auto_safe_fix_enabled`
- 开启 `cache_purge_enabled`
- index 提交先 `dry-run`，验证后切到 `webhook` 或 `google-indexing`
- outreach 先 `dry-run`，再切真实 provider

## 4. 每周运维流程（建议）

1. 看首页统计卡（通过/警告/失败）。
2. 跑一次 `Monitor diff` 看竞品变化。
3. 跑 `Index track` + `Index audit`。
4. 如果有 dropped/long_unindexed，先处理 P0/P1 问题。
5. 需要外联时执行 `Outreach plan -> run -> verify`。
6. 导出 markdown/csv 发给运营和技术。

## 5. 常见配置错误与修复

## 5.1 llms 文件不更新

- 检查站点根目录写权限。
- 检查安全插件/主机策略是否阻止写文件。
- 开启缓存联动，避免“实际已更新但前台还是旧内容”。

## 5.2 sitemap 端点返回 HTML 或跳转

- 开启 `enable_wp_endpoint_fix`。
- 检查 Nginx rewrite 是否抢先重写 `.xml/.txt`。
- Cloudflare 不要对 sitemap/robots/llms 做长期页面缓存。

## 5.3 track 结果波动大

- 保持固定扫描时段与相同 `index_search_depth`。
- 开启 `index_strict_search` 后会更“严格”，下降会更敏感。

## 5.4 自动修复担心影响前台样式

- 使用 `strict` 模式。
- strict 模式不会改 H1/H2 模板结构，也不会改 CSS/UI。

## 6. 配置完成后的验收脚本

```bash
for u in \
https://yourdomain.com/robots.txt \
https://yourdomain.com/sitemap.xml \
https://yourdomain.com/sitemap_index.xml \
https://yourdomain.com/wp-sitemap.xml \
https://yourdomain.com/llms.txt \
https://yourdomain.com/llms-full.txt
do
  echo "=== $u"
  curl -s -I "$u" | sed -n '1,8p'
done
```

验收标准：

- 全部 `HTTP 200`
- content-type 合理（txt/xml）
- 不出现异常跳转或缓存错乱
