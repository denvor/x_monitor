## ADDED Requirements

### Requirement: 浏览器参数由配置驱动
脚本启动时，SHALL 从 `config.ini` 的 `[chrome]` 配置段读取浏览器启动参数。

#### Scenario: 从 config.ini 读取代理地址
- **WHEN** `config.ini` 的 `[chrome]` 段包含 `proxy = http://127.0.0.1:20171`
- **THEN** Chrome 启动时添加 `--proxy-server=http://127.0.0.1:20171` 参数

#### Scenario: proxy 为 false 时不使用代理
- **WHEN** `config.ini` 的 `[chrome]` 段包含 `proxy = false`
- **THEN** Chrome 启动时**不**添加 `--proxy-server` 参数

#### Scenario: 从 config.ini 读取 user_data_dir
- **WHEN** `config.ini` 的 `[chrome]` 段包含 `user_data_dir = /tmp/custom-chrome`
- **THEN** Chrome 启动时添加 `--user-data-dir=/tmp/custom-chrome` 参数

#### Scenario: 缺少 [chrome] 配置段时使用默认值
- **WHEN** `config.ini` 中没有 `[chrome]` 段
- **THEN** proxy 为 None（不启用），user_data_dir 默认 `/tmp/xmonitor-chrome`

### Requirement: Config 类支持新字段
`Config` dataclass SHALL 包含 `proxy` 和 `user_data_dir` 字段。

#### Scenario: Config.load() 正确解析 chrome 段
- **WHEN** 调用 `Config.load()`
- **THEN** 返回的 Config 实例包含正确的 proxy 和 user_data_dir 值
