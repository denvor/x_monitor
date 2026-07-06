## ADDED Requirements

### Requirement: Chrome 用户数据持久化到固定目录
脚本 SHALL 通过 `--user-data-dir` 指定固定目录，确保 Chrome 跨运行复用同一用户数据 profile。

#### Scenario: 启动时使用固定用户数据目录
- **WHEN** nodriver 启动 Chrome
- **THEN** Chrome 使用 `/tmp/xmonitor-chrome` 作为用户数据目录

#### Scenario: 目录不存在时自动创建
- **WHEN** `/tmp/xmonitor-chrome` 目录不存在
- **THEN** Chrome 自动创建该目录

#### Scenario: Cookie 跨运行持久化
- **WHEN** 脚本首次运行后退出，再次运行
- **THEN** 前次运行的 Cookie 仍有效，无需重新注入

### Requirement: 与现有注入逻辑兼容
脚本 SHALL 保留现有的 cookie 注入逻辑作为回退。当固定目录中的 cookie 过期时，仍可通过 cookies.json 注入。

#### Scenario: Cookie 过期时自动回退
- **WHEN** 固定目录中的 cookie 过期，页面重定向到登录页
- **THEN** 脚本重新通过 CDP 注入 cookies.json 中的 cookie
