## 1. 日志轮转

- [ ] 1.1 修改 `_setup_logger()` 使用 `logs/` 目录和 `YYYYMMDD.log` 文件名
- [ ] 1.2 确保 `logs/` 目录不存在时自动创建

## 2. 推文备份

- [ ] 2.1 新增 `_backup_tweets()` 函数，将推文列表写入 `backup/` 目录
- [ ] 2.2 每个推文 ID 一个 JSON 文件，包含完整上下文（handle, fetched_at 等）
- [ ] 2.3 已存在时不覆盖，写入失败时静默跳过
- [ ] 2.4 在 `fetch_tweets()` 成功返回后调用 `_backup_tweets()`

## 3. 配置和文档

- [ ] 3.1 更新 `.gitignore` 忽略 `backup/` 和 `logs/` 目录
- [ ] 3.2 更新 CHANGELOG.md 记录变更
- [ ] 3.3 更新 README.md 中的日志路径和备份说明

## 4. 验证

- [ ] 4.1 运行脚本，确认日志写入 `logs/YYYYMMDD.log`
- [ ] 4.2 确认推文备份文件正确生成在 `backup/` 目录
- [ ] 4.3 确认项目根目录不再产生 `x_monitor.log`
- [ ] 4.4 提交并推送代码
