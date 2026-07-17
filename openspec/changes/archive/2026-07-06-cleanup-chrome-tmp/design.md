## Context

当前 `x_monitor_nodriver.py` 脚本每次运行时，nodriver 会在 `/tmp` 目录下创建 Chrome 的临时运行文件（如 `/tmp/com.google.Chrome.1sKw6c/`）。这些文件包含 Chrome 的用户数据、缓存、Cookie 等。每次运行都是全新 profile，导致：
1. Cookie 每次都要手动注入（cookies.json）
2. 每次启动 Chrome 都会创建新目录，增加启动时间
3. 目录只增不删，长期积累占用磁盘空间

## Goals / Non-Goals

**Goals:**
- 通过 `--user-data-dir` 指定固定目录，Chrome 跨运行复用同一 profile
- 去掉手动清理逻辑（目录固定后不再增长）
- 减少 Cookie 注入依赖（Chrome 自动保存登录态）

**Non-Goals:**
- 不清理旧的 `/tmp/com.google.Chrome.*` 目录（可手动删除）
- 不实现用户数据迁移（旧数据丢失可接受）

## Decisions

### 1. 数据持久化方案：browser_args 添加 --user-data-dir

**选择**：在 `_get_browser()` 的 `browser_args` 中添加 `--user-data-dir=/tmp/xmonitor-chrome`。

**备选方案**：
- 清理 + 重建 → 多此一举，既不复用也不解决问题
- 不处理，任由增长 → 磁盘空间浪费

**理由**：
- 一行代码解决，无需额外依赖
- Chrome 原生支持，稳定可靠
- 同一目录意味着 Cookie、LocalStorage 等登录态自动保存
- 目录大小固定后不再增长（Chrome 自己管理缓存上限）

### 2. 目录位置：/tmp/xmonitor-chrome

**选择**：使用 `/tmp/xmonitor-chrome` 作为固定目录。

**备选方案**：
- `/tmp/chrome-debug` → 可能与手动启动的 Chrome 冲突
- `./data/chrome` → 项目目录内，不利于.gitignore 管理

**理由**：
- `/tmp` 是标准的临时文件目录
- `/tmp` 在系统重启时自动清空，防止数据残留（可选）
- 命名明确（xmonitor-chrome），易于识别
- 不与 README 中手动启动 Chrome 使用的 `/tmp/chrome-debug` 冲突

### 3. Cookie 注入简化（可选）

注入 cookie 后，Chrome 会自动保存到 profile。下次运行时，这些 cookie 可能仍然有效。

**时机**：首次注入后，后续运行先尝试不注入直接访问 X。如果页面重定向到登录，则再次注入。

**实现**：此优化可选，不强制实现在当前变更中。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| Profile 损坏 | Chrome 启动失败 | 删除 `/tmp/xmonitor-chrome` 后重试 |
| Cookie 过期 | 需重新注入 | 保留现有注入逻辑作为回退 |
| `/tmp` 重启后清空 | 需重新注入 cookie | `/tmp` 特性，自动恢复 |
| 多实例冲突 | 两个脚本无法同时运行 | 共享同一 profile，由 Chrome 管理锁定 |

## Migration Plan

1. 修改 `_get_browser()` 的 `browser_args`，添加 `--user-data-dir=/tmp/xmonitor-chrome`
2. 清理 `/tmp` 下旧的 `com.google.Chrome.*` 目录（手动或脚本退出时顺带删除）
3. 更新 README.md 说明 profile 持久化
4. 更新 CHANGELOG.md 记录变更
