## Why

每次运行 `x_monitor_nodriver.py` 脚本时，nodriver 会在 `/tmp` 目录下创建 Chrome 的临时运行文件（如 `/tmp/com.google.Chrome.1sKw6c/`）。这些文件包含 Chrome 的用户数据、缓存、Cookie 等。脚本运行结束后，这些临时文件不会被回收：

1. **磁盘浪费**：每次运行创建新目录（~50MB），长期积累占用大量空间
2. **Cookie 重复注入**：每次运行都是全新 profile，必须手动注入 cookies.json
3. **启动慢**：每次创建新 profile 比复用已有 profile 慢

## What Changes

- **Chrome 用户数据持久化**：通过 `--user-data-dir=/tmp/xmonitor-chrome` 指定固定目录，每次运行复用同一 profile
- **去除**：不再需要清理逻辑，因为目录固定且不再增长
- **去除了**：不再依赖下游的清理逻辑（如 cron 清理 /tmp）

## Capabilities

### New Capabilities
- `chrome-userdata-persist`: Chrome 用户数据持久化到固定目录，跨运行复用

### Modified Capabilities

（无现有 spec 需要修改）

## Impact

- `x_monitor_nodriver.py` — `browser_args` 中添加 `--user-data-dir=/tmp/xmonitor-chrome`
- `/tmp` 目录 — `/tmp/com.google.Chrome.*` 不再创建，只保留一个固定目录 `/tmp/xmonitor-chrome`
- Cookie 注入可能不需要了（Chrome 自动保存登录态）
