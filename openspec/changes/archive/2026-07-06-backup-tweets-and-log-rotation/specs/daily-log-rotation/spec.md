## ADDED Requirements

### Requirement: 日志按日期轮转
脚本启动时，日志文件应存放在 `logs/` 目录下，文件名格式为 `YYYYMMDD.log`，每天一个文件。

#### Scenario: 启动时创建当日日志文件
- **WHEN** 脚本启动
- **THEN** 日志写入 `logs/YYYYMMDD.log`，其中 YYYYMMDD 为当天日期（如 `20260529.log`）

#### Scenario: 日志目录不存在时自动创建
- **WHEN** `logs/` 目录不存在
- **THEN** 自动创建该目录

#### Scenario: 跨天运行时自动切换日志文件
- **WHEN** 脚本运行跨越午夜（日期变更）
- **THEN** 后续日志写入新的 `YYYYMMDD.log` 文件

#### Scenario: 日志写入失败时回退到 stderr
- **WHEN** `logs/` 目录写入失败（如磁盘满、权限不足）
- **THEN** 日志输出到 stderr，不抛出异常

### Requirement: 项目根目录不再存放日志
- **WHEN** 脚本运行
- **THEN** 项目根目录不再产生 `x_monitor.log` 文件
