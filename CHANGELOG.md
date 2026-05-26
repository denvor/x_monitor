# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
