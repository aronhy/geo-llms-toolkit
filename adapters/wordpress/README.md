# GEO LLMS Auto Regenerator

发布或更新已发布内容时，自动重建站点根目录：

- `llms.txt`
- `llms-full.txt`

同时提供：

- GEO 端点扫描：`robots.txt`、`sitemap.xml`、`sitemap_index.xml`、`wp-sitemap.xml`、`llms.txt`、`llms-full.txt`
- SEO / GEO 信号扫描：首页 `H1`、首页 `<link rel="llms" href="/llms.txt">`、`canonical`、`og:*` / `twitter:*`、`og:image`、文章 `Article` schema、作者页信号、`BreadcrumbList`、`Organization.sameAs`、软 404、`noindex` 冲突、首页/文章页抓取一致性、低价值页 `noindex`
- 一键安全修复：启用低价值 llms 过滤、低价值页 `noindex`、基础 OG/Twitter 与 schema 补全（未检测到常见 SEO 插件时）
- 自动安全修复：扫描后根据问题自动执行（llms 缺失重建、LLMS Link 注入、低价值页 noindex、WP 层端点修复）
- 安全模式分级：`Strict`（默认，只做低风险，不动 H1/H2/CSS/UI）与 `Balanced`（额外补 OG/Twitter + Schema）
- 安全修复预览：`dry run`、预计变更说明、回滚上次修复、恢复默认设置
- 前台输出：首页 `<head>` 自动声明 `llms.txt` 位置
- LLMS 规则中心：选择纳入的内容类型与分类、手动 Pin、全局排除规则、单篇自定义 llms 摘要
- 兼容环境识别：Yoast / Rank Math / AIOSEO / WooCommerce / 静态首页博客页 / Polylang / WPML
- 定时扫描与历史趋势：支持每日/每周扫描，保存历史结果与趋势变化
- GEO Agent 闭环：可在定时任务中执行“扫描 -> 自动修复 -> 复扫 -> 回滚”
- 通知能力：支持邮件与 Webhook，可自定义模板，占位符已预设
- 缓存联动：重建 llms 后，可选清理 Cloudflare 和常见 WordPress 页面缓存
- 报告导出：支持导出 Markdown / JSON / CSV GEO 报告
- 产品基础设施：配置导入/导出、卸载清理、版本迁移、错误日志、后台权限控制、textdomain 加载

## 安装

1. 在仓库根目录构建可安装 ZIP：
   - `./scripts/build-wordpress-zip.sh`
2. 构建产物在：
   - `dist/geo-llms-auto-regenerator-<version>.zip`
3. 在 WP 后台安装：
   - `插件 -> 安装插件 -> 上传插件`
   - 选择上面的 ZIP 并启用 `GEO LLMS Auto Regenerator`
4. 后台可手动触发：
   - `设置 -> GEO LLMS Auto -> 立即重建 llms 文件`

详细环境配置（宝塔 + Nginx + Cloudflare）：
- `../../docs/wordpress-detailed-setup.md`

## 触发条件

- 新发布文章/页面
- 已发布内容更新
- 已发布内容删除/恢复

## 后台功能

- `设置 -> GEO LLMS Auto`
- 可手动执行：
  - `立即重建 llms 文件`
  - `立即扫描 GEO`
  - `预览安全修复`
  - `一键应用安全修复`
  - `回滚上次修复`
  - `恢复默认设置`
- 可配置：
  - 后台权限
  - 错误日志
  - 卸载时是否自动清理数据
  - 纳入的内容类型
  - 纳入的分类 / 类目
  - 手动 Pin 内容
  - 全局排除规则
  - llms 是否排除低价值页
  - 低价值页是否自动 `noindex`
  - 安全修复模式（Strict / Balanced）
  - 是否自动输出首页 LLMS Link
  - 是否启用 WP 层端点修复（robots/sitemap）
  - 是否输出基础 OG/Twitter
  - 是否输出基础 schema
  - 扫描后是否自动执行安全修复
  - 手动扫描是否也自动执行安全修复
  - 是否启用 GEO Agent 闭环模式（定时扫描时生效）
  - 是否启用缓存联动
  - Cloudflare Zone ID / API Token / 清理模式
  - 额外清理 URL
  - 定时扫描频率 / 星期 / 时间 / 历史保留条数
  - 邮件通知 / Webhook 通知
  - 邮件标题模板 / 正文模板 / Webhook 模板
  - `Organization Logo URL`
  - `Organization sameAs`
- 可导出：
  - GEO 报告 Markdown / JSON / CSV
  - 完整插件配置 JSON
- 可导入：
  - 完整插件配置 JSON
- 编辑页侧边栏：
  - `Custom llms 摘要`
  - `Pin 到 llms`
  - `从 llms 排除`

## 输出位置

- `ABSPATH/llms.txt`
- `ABSPATH/llms-full.txt`

如果站点根目录不可写，自动重建会失败。请先确认 Web 用户对站点根目录有写权限。
