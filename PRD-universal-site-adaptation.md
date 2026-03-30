# PRD：全站点适配升级（CLI 优先，WP 写入保留）

## 1. 背景与目标

当前项目虽有 standalone CLI，但扫描判断仍有明显 WordPress 偏置（例如 `wp-sitemap.xml` 对非 WP 站误报 fail）。

本期目标：

- 让任意站点都能稳定执行“扫描/诊断/报告”。
- 通过平台识别与规则配置，降低跨 CMS 误报。
- 保留 WordPress 自动修复能力，非 WP 站点仅输出建议，不直接修改站点。

## 2. 交付边界（本期）

- 范围：CLI 优先落地。
- 规则配置：JSON（`.geo-rules.json`）。
- 自动修复策略：仅 WordPress 可自动修复；Shopify/Webflow/Ghost/Custom 只读。
- 不重构 WordPress 插件 UI。

## 3. 核心能力设计

### 3.1 入口自动发现（Sitemap Discovery）

发现顺序：

1. `robots.txt` 中 `Sitemap:` 声明
2. 默认路径回退（`/sitemap.xml`、`/sitemap_index.xml`、`/wp-sitemap.xml`）
3. 首页 HTML 的 sitemap/feed 线索

输出要求：

- 在 `scan` 报告中输出 discovery 诊断：
- 候选来源与 URL
- 每个候选探测状态与失败原因
- 最终命中的 active sitemaps

### 3.2 平台指纹识别（Platform Fingerprint）

支持识别：

- `wordpress`
- `shopify`
- `webflow`
- `ghost`
- `custom`

输出要求：

- `meta.platform`
- `meta.platform_confidence`
- `meta.platform_scores`
- `meta.platform_evidence`

并支持 CLI 强制覆盖：

- `--platform-profile auto|wordpress|shopify|webflow|ghost|custom`

### 3.3 规则适配（按平台切换检查）

原则：

- WP 专属检查（例如 `wp-sitemap.xml`）仅在“检测为 WP 且置信度足够高”时可计 fail。
- 非 WP 平台对 WP 专属检查降级为 `warn` 或 `skip` 表达，不作为硬失败主因。

### 3.4 读写分层（Adapter Capabilities）

扩展 adapter contract：

- `get_capabilities() -> AdapterCapabilities`
- 字段：
- `can_write_index_files`
- `can_auto_fix`
- `can_purge_cache`

适配器策略：

- `StandaloneWebAdapter`：可写 index（本地 output_dir 存在时）。
- `ShopifyReadOnlyAdapter`：只读。
- `GenericHttpReadOnlyAdapter`：只读。

### 3.5 规则配置化（Rules）

根目录配置文件：`.geo-rules.json`

首版配置块：

- `low_value_patterns`
- `keyword_quality`
- `noindex_policy`
- `schema_requirements`
- `platform_overrides`

优先级：

1. CLI 显式参数
2. `.geo-rules.json`
3. 内置默认值

## 4. CLI 与输出变更

### 4.1 新增 CLI 参数

- `geo scan --rules-file`
- `geo scan --platform-profile`
- `geo all --rules-file`
- `geo all --platform-profile`
- `geo adapter-check --platform-profile`

### 4.2 输出字段新增

- `scan.meta.platform`
- `scan.meta.platform_confidence`
- `scan.meta.discovery`
- `scan.meta.rules_file`
- `scan.meta.rules_warnings`
- `scan.checks[*].applicability`

兼容性要求：

- 保持原命令与原字段可用，仅追加字段，不删除旧字段。

## 5. 里程碑

### M1：入口自动发现 + 诊断输出

- 完成 robots/default/homepage 三段式 sitemap 发现。
- `scan` 报告增加 discovery 诊断信息。

### M2：平台指纹 + 规则适配

- 完成 platform fingerprint。
- WP 专属检查仅在 WP 高置信度下计 fail。
- 引入 `.geo-rules.json` 加载与默认合并。

### M3：能力分层 + 只读适配器

- contract 增加 capabilities。
- 新增 Shopify/Generic 只读适配器并接入 `adapter-check`。

## 6. 验收标准

- 非 WP 站点扫描中，`wp-sitemap.xml` 不再成为硬 fail 主因。
- 至少 90% 样本站点能在报告中看到可解释的 sitemap 发现路径。
- 报告包含平台识别证据与置信度。
- WordPress 现有自动修复行为不变。

## 7. 风险与回退

- 风险：平台识别误判导致规则误用。
- 控制：保留 `--platform-profile` 强制覆盖，且低置信度默认回退 `custom`。
- 回退：出现异常时，规则系统退回内置默认，不中断扫描流程。
