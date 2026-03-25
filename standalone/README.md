# Standalone CLI (非 WordPress 版本)

这个版本适合任意网站（静态站、Shopify、自建站、CMS）。

## 1. 环境要求

- Python 3.9+
- 可访问目标网站公网地址

## 2. 安装部署

```bash
git clone https://github.com/aronhy/geo-llms-toolkit.git
cd geo-llms-toolkit
chmod +x geo
./geo --help
```

## 3. 快速上手（最短流程）

```bash
# A) 站点诊断
./geo scan aronhouyu.com

# B) 生成 llms 文件
./geo llms aronhouyu.com --output-dir ./output

# C) 竞品监控（需要关键词文件）
./geo monitor aronhouyu.com \
  --keywords-file ./examples/keywords.txt \
  --discover-competitors \
  --output ./output/monitor.json \
  --format json

# D) 外联计划与执行
./geo outreach plan \
  --monitor-report ./output/monitor.json \
  --pitch-url https://aronhouyu.com/your-page \
  --site-name "Aron Houyu" \
  --output-dir ./output/outreach

./geo outreach run \
  --campaign-file ./output/outreach/outreach-campaign.json \
  --provider dry-run

# E) 收录闭环
./geo index discover aronhouyu.com --output ./output/index-discover.json --format json
./geo index track aronhouyu.com --discover-report ./output/index-discover.json --output ./output/index-track.json --format json
./geo index audit aronhouyu.com --from-track-report ./output/index-track.json --output ./output/index-audit.md
./geo index report aronhouyu.com --history-dir ./.geo-history/index --days 30 --output ./output/index-report.md
```

## 4. 命令模块说明

- `geo scan`: 基础 GEO/SEO 信号与端点检查
- `geo llms`: 生成 `llms.txt` / `llms-full.txt`
- `geo monitor`: 关键词维度竞品监控 + 优先级动作建议
- `geo outreach plan/run/status/verify/update`: 外联计划、执行、状态跟踪、回访
- `geo index discover/track/submit/audit/report`: 收录发现、跟踪、提交、诊断、周报

## 5. 常见部署方式

- 本地运行：手工执行命令
- 服务器定时任务：用 `cron` 定时跑 `monitor` / `index track` / `index report`
- CI/CD：在 GitHub Actions 或其他流水线跑 CLI 并上传报告

## 6. 产出文件

- `output/llms.txt`
- `output/llms-full.txt`
- `output/monitor.json`
- `output/outreach/*`
- `output/index-*.json|md|csv`
- `.geo-history/*`（历史快照）

## 7. 生产建议

- 先用 `dry-run` 验证流程
- `index submit` 先做小批量
- 保留 `.geo-history`，用于趋势分析与回归排查
