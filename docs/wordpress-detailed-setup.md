# WordPress 详细配置指南（宝塔 + Nginx + Cloudflare）

本文是 `GEO LLMS Auto Regenerator` 的实操配置文档，目标是一次性把站点跑通到可持续运行状态。

## 1. 前置条件
- WordPress 可正常访问，固定链接已启用（非“朴素”）。
- 站点根目录可写（用于写入 `llms.txt` 和 `llms-full.txt`）。
- 已安装并启用插件。
- 如使用 Cloudflare，已准备 Zone Token（可选）。

## 2. 安装插件（ZIP）
1. 进入仓库根目录，构建 ZIP：
```bash
./scripts/build-wordpress-zip.sh
```
2. 在 WordPress 后台上传并启用：
- `插件 -> 安装插件 -> 上传插件`
- 选择 `dist/geo-llms-auto-regenerator-<version>.zip`

## 3. 首次初始化（推荐默认）
后台路径：`设置 -> GEO LLMS Auto`

按下面顺序操作：
1. 点一次 `立即重建 llms 文件`。
2. 点一次 `立即扫描 GEO`。
3. 点 `预览安全修复`，确认后再 `一键应用安全修复`。

建议开关（初次上线）：
- 安全模式：`Strict`
- 扫描后自动执行安全修复：`开启`
- 手动扫描也自动修复：`关闭`（先人工确认）
- 启用定时扫描：`开启（每周）`
- Fail 通知：`开启`
- Warn 通知：按需

## 4. Nginx 端点放行（关键）
目标：这 4 个地址必须直接 `200` 且不跳转：
- `/robots.txt`
- `/sitemap.xml`
- `/sitemap_index.xml`
- `/wp-sitemap.xml`

在宝塔站点 `Nginx 配置` 中，把下面片段放在通用 rewrite 之前：

```nginx
location = /robots.txt { try_files $uri =404; }
location = /sitemap.xml { try_files $uri /index.php?$args; }
location = /sitemap_index.xml { try_files $uri /index.php?$args; }
location = /wp-sitemap.xml { try_files $uri /index.php?$args; }
```

说明：
- `robots.txt` 通常建议走静态文件。
- `wp-sitemap.xml` 通常由 WordPress 动态输出。
- 若你用 SEO 插件输出 sitemap，`sitemap.xml/sitemap_index.xml` 也可由插件接管。

## 5. Cloudflare 配置建议
- Caching Level：`Standard`
- 不要对 `robots.txt` / `sitemap*.xml` / `wp-sitemap.xml` / `llms*.txt` 做“Cache Everything”长期缓存。
- 如已配置缓存联动，插件内填写：
  - `Zone ID`
  - `API Token`
  - 清理模式（建议按 URL 清理）

## 6. llms 规则中心（推荐起步）
- 纳入内容类型：仅 `post`/`page`（先保守）
- 排除规则：登录、找回密码、示例页、空标题页
- 手动 Pin：你的核心转化页、品牌页、高价值教程页
- 单篇自定义 llms 摘要：给重点文章补 2-3 句摘要

## 7. 验证清单（上线必做）
在服务器执行：

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
- HTTP 状态：`200`
- Content-Type：符合文本或 XML 预期
- 不出现异常跳转循环

## 8. 定时扫描与通知（最小可用）
- 频率：每周
- 历史保留：20 条
- 通知邮箱：运营/技术公共邮箱
- Webhook：先不启用也可以

建议模板内容至少包含：
- 本次 Pass/Warn/Fail 数
- 新增 Fail 项
- 推荐处理动作

## 9. 常见问题

### Q1: `llms*.txt` 没更新
- 检查站点根目录写权限。
- 检查是否有安全插件拦截写文件。

### Q2: `wp-sitemap.xml` 返回 HTML
- 先确认是否存在同名静态文件覆盖。
- 再检查 Nginx rewrite 是否抢先重写了 `.xml`。

### Q3: 扫描结果和前台不一致
- 先清 Cloudflare 缓存。
- 再清服务器页面缓存/插件缓存。

### Q4: 自动修复后担心误改
- 使用 `Strict` 模式。
- 先 `预览安全修复`，再应用。
- 随时使用 `回滚上次修复`。

## 10. 运营建议（稳定后）
- 每周固定看一次趋势，不要每天盯。
- 新文章发布后，抽查 `llms-full.txt` 是否纳入。
- 每月复查一次排除规则和 Pin 列表。
