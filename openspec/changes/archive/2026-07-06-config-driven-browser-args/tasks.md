## 1. 修改 Config 类

- [x] 1.1 Config dataclass 新增 `proxy: Optional[str] = None` 和 `user_data_dir: str = "/tmp/xmonitor-chrome"` 字段
- [x] 1.2 `Config.load()` 中读取 `[chrome]` 段：proxy 为字符串或 None（false 时），user_data_dir 有 fallback

## 2. 修改 _get_browser()

- [x] 2.1 将 `config` 参数传递给 `_get_browser()`
- [x] 2.2 `browser_args` 动态组装：`--disable-dev-shm-usage` + 可选 `--proxy-server` + `--user-data-dir`
- [x] 2.3 更新 `fetch_tweets()` 调用 `_get_browser(config)` 传递 config

## 3. 更新 config.ini 和样本

- [x] 3.1 `config.ini` 追加 `[chrome]` 段
- [x] 3.2 更新 `config.ini.sample`
- [x] 3.3 更新 CHANGELOG.md
- [x] 3.4 提交并推送代码
