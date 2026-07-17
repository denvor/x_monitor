## ADDED Requirements

### Requirement: 推文按 ID 持久化备份
脚本每次成功抓取到推文后，应将每条推文的数据持久化为独立的 JSON 文件，存放在 `backup/` 目录下。

#### Scenario: 成功抓取后备份推文
- **WHEN** `fetch_tweets()` 成功返回推文列表
- **THEN** 每条推文写入一个 `backup/<tweet_id>.json` 文件，包含 `id`, `text`, `link`, `pubTime`, `handle`, `fetched_at` 字段

#### Scenario: 推文已存在时不覆盖
- **WHEN** `backup/<tweet_id>.json` 已存在
- **THEN** 跳过该推文，不覆盖已有文件

#### Scenario: 备份目录不存在时自动创建
- **WHEN** `backup/` 目录不存在
- **THEN** 自动创建该目录

#### Scenario: 备份失败时不阻塞主流程
- **WHEN** 写入 `backup/` 目录失败（如磁盘满、权限不足）
- **THEN** 记录警告日志，不抛出异常，不影响推文去重和飞书推送

### Requirement: 备份文件包含完整上下文
每个备份 JSON 文件应包含足够的上下文信息，便于事后回溯。

#### Scenario: 备份文件包含 handle 和抓取时间
- **WHEN** 写入 `backup/<tweet_id>.json`
- **THEN** 文件包含 `handle`（账号名）和 `fetched_at`（ISO 8601 时间戳）
