## Context

当前 `_get_browser()` 中 `browser_args` 是硬编码列表：
```python
browser_args=[
    "--disable-dev-shm-usage",
    "--proxy-server=http://127.0.0.1:20171",
    "--user-data-dir=/tmp/xmonitor-chrome",
],
```
用户需要代理地址和 user-data-dir 可配置，且 proxy 可设 false 来禁用。

## Goals / Non-Goals

**Goals:**
- `config.ini` 新增 `[chrome]` 段，包含 `proxy` 和 `user_data_dir`
- `proxy = false` 时不添加 `--proxy-server` 参数
- `Config` 类加载新配置，`_get_browser()` 动态组装 browser_args
- `config.ini.sample` 同步更新

**Non-Goals:**
- 不做代理地址校验（字符串按原样传递给 Chrome）
- 不实现多个代理切换

## Decisions

### 1. 配置格式

**选择**：`[chrome]` 段，proxy 值为 url 字符串或布尔值 false。

```ini
[chrome]
proxy = http://127.0.0.1:20171
user_data_dir = /tmp/xmonitor-chrome
```

禁用时：
```ini
[chrome]
proxy = false
user_data_dir = /tmp/xmonitor-chrome
```

**理由**：
- `str` 类型正常使用，`"false"` 字面量时跳过 — configparser 读取后判断逻辑清晰
- 与现有配置风格一致

### 2. Config 类扩展

**选择**：在现有 `Config` dataclass 中新增 `proxy: Optional[str]` 和 `user_data_dir: str` 字段。

**理由**：
- 保持一致：现有配置已使用 Config class
- 避免新增配置类或模块

### 3. browser_args 动态组装

**选择**：`_get_browser()` 接受一个 `config` 参数，根据配置动态构造列表。

```python
browser_args = ["--disable-dev-shm-usage"]
if config.proxy:
    browser_args.append(f"--proxy-server={config.proxy}")
browser_args.append(f"--user-data-dir={config.user_data_dir}")
```

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| config.ini 无 `[chrome]` 段 | Config 使用 fallback 默认值 | `cfg.get()` fallback 留空，proxy 为 None 时不使用 |
| user_data_dir 为空 | Chrome 使用默认临时目录 | fallback 设为 /tmp/xmonitor-chrome |

## Migration Plan

1. `config.ini` 追加 `[chrome]` 段
2. `Config` 类新增 `proxy` 和 `user_data_dir` 字段、load() 加载逻辑
3. `_get_browser()` 改为从 `Config` 实例读取参数，动态组装 browser_args
4. 更新 `config.ini.sample`
5. 更新 CHANGELOG.md
