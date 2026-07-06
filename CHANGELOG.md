# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Chrome 用户数据持久化**：`--user-data-dir=/tmp/xmonitor-chrome` 固定目录，跨运行复用同一 profile，避免每次创建 196MB 临时目录
- **浏览器参数配置化**：`--proxy-server` 和 `--user-data-dir` 从硬编码改为由 `config.ini` 的 `[chrome]` 段指定；`proxy = false` 时禁用代理

## [4.3] - 2026-05-29

### Added
- **推文备份**：每次抓取到的推文按 ID 持久化为独立 JSON 文件，存放在 `backup/` 目录
- **日志轮转**：日志从项目根目录迁移到 `logs/` 目录，按 `YYYYMMDD.log` 命名

### Changed
- **BREAKING**：`x_monitor.log` 不再存放在项目根目录

## [4.2] - 2026-05-29

### Added
- **Chrome 自动启动**：当 9222 端口不可用时，自动启动 Xvfb 虚拟显示器并通过 nodriver 启动 Chrome（含代理），无需手动预先启动 Chrome
- **端口检测**：`_check_port()` 在启动前检测 CDP 端口是否可用

### Fixed
- **Cookie 注入时序**：注入 cookie 后重新导航页面，确保 cookie 在请求中生效（修复注入后页面未登录的问题）
- **双重 `_launch_chrome()` 调用**：合并端口检测和 DISPLAY 检测条件，避免重复启动 Xvfb

### Changed
- **Chrome 启动参数**：`--proxy-server=http://127.0.0.1:20171` 内置于 browser_args，自动配置代理

## [4.1] - 2026-05-26

### Fixed
- **推文提取重复**：JS 提取阶段增加 link 去重，解决 X DOM 中同一推文以不同形式（主卡片 + 缩略预览）出现导致重复推送的问题

### Changed
- **Chrome 启动命令**：添加 `--proxy-server="http://127.0.0.1:20171"` 代理配置，不再依赖系统代理

## [4.0] - 2026-05-15

### Added
- **Cookie 注入机制**：通过 CDP `Network.setCookies` 注入 cookies.json 中的 cookie，绕过 nodriver 检测
- **飞书 REST API 推送**：改为直接调用飞书 API，含 token 缓存机制
- **部署步骤**：添加详细的 Chrome 启动、配置和运行说明
- **Cookie 文件脱敏**：`cookies.json` 加入 `.gitignore`，提供 `cookies.json.sample` 模板

### Changed
- 架构从 v1 (curl_cffi) 升级为 v2 (nodriver + 接管已有 Chrome)

## [3.0] - Previous

### Added
- 核心抓取逻辑（nodriver + JS evaluate）
- 推文去重（数值 ID 比较）
- 置顶推文过滤
- 重试机制
- 结构化日志

---

*Note: Version numbers are documentation references, not release tags.*
