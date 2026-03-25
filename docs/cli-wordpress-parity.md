# CLI -> WordPress Parity Matrix

更新时间：2026-03-25

## 1) 已对齐（功能级）

| CLI 能力 | WordPress 对应 |
| --- | --- |
| `geo scan` | 后台 `立即扫描 GEO` + 导出报告 |
| `geo llms` | 自动/手动重建 `llms.txt` 和 `llms-full.txt` |
| `geo all` | `运行 GEO Agent`（扫描 + 修复 + 复扫） |
| `geo monitor` | `运行 Monitor` + Monitor 配置项 |
| `geo monitor-diff` | `运行 Monitor Diff` + 导出 |
| `geo outreach plan/run/verify/status/update` | 工作台对应按钮全覆盖 |
| `geo index discover/track/submit/audit/report` | 工作台对应按钮全覆盖 |
| `--strict-search` | `Index Strict Search` 设置 |
| `--alert-on-drop`/`--alert-webhook*` | Index 掉索引告警设置 |
| `--notification-type` | Index `URL_UPDATED/URL_DELETED` 设置 |
| `--include-existing` | Outreach `include_existing` |
| `--run-followup-due` | Outreach `run_followup_due` |
| `--brand-token` | Monitor `品牌词 Token` |
| `--exclude-domain` | Outreach `排除域名` |
| `--enrich-contacts` | Outreach `自动探测联系人` |
| `--apify-allow-fallback-first` | Outreach `Apify fallback-first` |

## 2) 语义映射（实现方式不同）

| CLI 参数 | WordPress 说明 |
| --- | --- |
| `--history-dir` | WP 使用 option 历史存储（无需路径） |
| `--campaign-file` / `--state-file` | WP 使用数据库持久化 campaign/state |
| `--output-dir` / `--output` | WP 使用后台导出（Markdown/JSON/CSV 下载） |
| `--monitor-report` | WP 直接使用上次 Monitor 结果 |
| `--discover-report` / `--from-track-report` | WP 在 Index 工作台内用 discover/track 状态衔接 |

## 3) 结论

- 当前 WordPress 版本已覆盖 CLI 核心工作流与运营参数控制。
- 差异主要是“文件参数”在 WP 中被“后台状态与按钮流”替代，不影响能力闭环。
