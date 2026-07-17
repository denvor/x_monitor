## 1. 实现用户数据持久化

- [x] 1.1 在 `_get_browser()` 的 `browser_args` 中添加 `--user-data-dir=/tmp/xmonitor-chrome`

## 2. 清理旧临时目录

- [x] 2.1 检查并删除 `/tmp` 下旧的 `com.google.Chrome.*` 临时目录（手动或脚本退出时顺带清理）

## 3. 验证

- [x] 3.1 运行脚本，确认 `/tmp/xmonitor-chrome` 目录已创建（196MB profile，永久化成功）
- [x] 3.2 再次运行脚本，确认 Chrome 复用同一目录（无新的 `com.google.Chrome.*` 目录）
- [x] 3.3 确认 cookie 注入仍正常工作（作为回退机制）
- [x] 3.4 更新 CHANGELOG.md 记录变更
- [x] 3.5 提交并推送代码
