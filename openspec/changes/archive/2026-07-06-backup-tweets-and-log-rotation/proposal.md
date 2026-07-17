## Why

当前脚本每次抓取到的推文仅用于去重和飞书推送，抓取结果不持久化，无法回溯历史推文内容。同时日志文件 `x_monitor.log` 存放在项目根目录且只增不减，随着运行时间增长文件会越来越大，不利于查看和分析。

## What Changes

- **推文备份**：每次抓取到的推文按推文 ID 存储为独立的 JSON 文件，存放在 `backup/` 目录下
- **日志轮转**：日志文件从项目根目录迁移到 `logs/` 目录，按日期 `YYYYMMDD.log` 命名，每天一个文件
- **BREAKING**：`x_monitor.log` 不再存放在项目根目录，旧日志文件不再自动迁移

## Capabilities

### New Capabilities
- `tweet-backup`: 将每次抓取的推文按 ID 持久化为 JSON 文件，存放在 `backup/` 目录
- `daily-log-rotation`: 日志按日期轮转，存放在 `logs/` 目录，每天一个文件

### Modified Capabilities

（无现有 spec 需要修改）

## Impact

- `x_monitor_nodriver.py` — 新增推文备份逻辑和日志路径变更
- `x_monitor.log` — 不再使用，新日志存放到 `logs/`
- 项目根目录结构变化：新增 `backup/` 和 `logs/` 目录
