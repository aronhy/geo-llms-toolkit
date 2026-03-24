# geo-llms-toolkit

Open-source GEO toolkit for website discovery, crawlability, and AI-readable index generation.

Current status:

- `adapters/wordpress` is production-ready and includes:
- auto-regeneration of `llms.txt` and `llms-full.txt`
- GEO endpoint/signal scan
- safe-fix workflow
- report export (Markdown / JSON / CSV)
- optional cache purge integration

Next target:

- extract reusable engine into `core`
- add non-WordPress adapters (`shopify`, `standalone-cli`)

## Why this repo

The original project was WordPress-focused. This repository is the open-source foundation to make GEO checks and LLMS indexing usable across platforms.

## Repository layout

```text
geo-llms-toolkit/
  adapters/
    wordpress/        # current working adapter (PHP plugin)
  core/
    docs/             # engine contracts and design docs
  docs/
    architecture.md
    roadmap.md
    migration-plan.md
  examples/
```

## 下载与使用教程

### 1. 下载项目

方式 A：`git clone`

```bash
git clone https://github.com/aronhy/geo-llms-toolkit.git
cd geo-llms-toolkit
```

方式 B：直接下载 ZIP

```text
GitHub 页面 -> Code -> Download ZIP -> 解压
```

### 2. 打包 WordPress 适配器

```bash
cd adapters/wordpress
zip -r geo-llms-auto-regenerator.zip .
```

### 3. 安装到 WordPress

```text
WP 后台 -> 插件 -> 安装插件 -> 上传插件
选择 geo-llms-auto-regenerator.zip -> 启用
设置 -> GEO LLMS Auto
```

### 4. 首次验证

在插件页面执行：

- `立即重建 llms 文件`
- `立即扫描 GEO`

## Quick start (WordPress adapter)

1. Go to `adapters/wordpress`.
2. Install plugin ZIP from that adapter or upload plugin files into WordPress.
3. Enable plugin and configure settings in `Settings -> GEO LLMS Auto`.

See adapter docs:

- [WordPress adapter readme](./adapters/wordpress/readme.txt)

## 命令教程

### 常用开发命令

```bash
# 进入项目
cd geo-llms-toolkit

# 检查 WordPress 适配器语法
php -l adapters/wordpress/geo-llms-auto-regenerator.php
php -l adapters/wordpress/uninstall.php
php -l adapters/wordpress/languages/index.php
```

### 提交代码

```bash
git add .
git commit -m "feat: your change"
git push origin main
```

### 发布版本（示例）

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Open-source policy

- License: `GPL-2.0-or-later`
- Issues and pull requests are welcome
- Keep platform-neutral logic in `core`, not in adapter-specific code

## Roadmap

See [docs/roadmap.md](./docs/roadmap.md).

## Follow

- X: https://x.com/aronhouyu
- YouTube: https://www.youtube.com/@aronhouyu1024
