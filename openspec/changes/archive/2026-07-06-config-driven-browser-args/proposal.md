## Why

当前 `x_monitor_nodriver.py` 中 `--proxy-server` 和 `--user-data-dir` 是硬编码的字符串，缺乏灵活性：

1. 无法在不修改代码的情况下更改代理地址或禁用代理
2. Chrome 用户数据目录路径固定，无法在不同环境间切换
3. 运维需求：有时需要关闭代理直连，有时需要更换代理地址

## What Changes

- **config.ini 新增 `[chrome]` 配置段**：包含 `proxy` 和 `user_data_dir` 两个字段
- **proxy 可禁用**：`proxy = false` 时，不添加 `--proxy-server` 参数
- **proxy 自定义地址**：`proxy = http://127.0.0.1:20171` 时，正常使用代理
- **user_data_dir 可配置**：自定义 Chrome 用户数据目录路径

## Capabilities

### New Capabilities
- `config-driven-browser-args`: 浏览器启动参数由 config.ini 配置驱动

### Modified Capabilities

（无现有 spec 需要修改）

## Impact

- `config.ini` — 新增 `[chrome]` 配置段
- `x_monitor_nodriver.py` — `Config` 类新增字段，`_get_browser()` 动态组装 browser_args
- `config.ini.sample` — 更新模板
- 向后兼容：缺少 `[chrome]` 配置段时使用默认值
