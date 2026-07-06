# Backup Tweets and Daily Log Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次抓取到的推文按 ID 持久化为独立 JSON 文件到 `backup/` 目录，日志按日期轮转到 `logs/YYYYMMDD.log`。

**Architecture:** 两个独立功能，均内联到现有 `x_monitor_nodriver.py` 中，不引入新模块。日志轮转修改 `_setup_logger()` 的 FileHandler 路径；推文备份新增 `_backup_tweets()` 函数，在 `fetch_tweets()` 成功返回后调用。

**Tech Stack:** Python 3.12, asyncio, json, logging, os, datetime（全部 stdlib）

---

## Task 1: 日志轮转 — 修改 `_setup_logger()`

**Files:**
- Modify: `x_monitor_nodriver.py:149-160`

- [ ] **Step 1: 修改 FileHandler 路径**

  将 `_setup_logger()` 中的 FileHandler 从项目根目录的 `x_monitor.log` 改为 `logs/YYYYMMDD.log`。

  当前代码（line 152-155）：
  ```python
  _dir = os.path.dirname(os.path.abspath(__file__))
  fmt_ts = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
  fmt_flat = logging.Formatter("%(message)s")
  fh = logging.FileHandler(os.path.join(_dir, "x_monitor.log"), encoding="utf-8")
  ```

  替换为：
  ```python
  _dir = os.path.dirname(os.path.abspath(__file__))
  _log_dir = os.path.join(_dir, "logs")
  os.makedirs(_log_dir, exist_ok=True)
  _today = datetime.now().strftime("%Y%m%d")
  _log_file = os.path.join(_log_dir, f"{_today}.log")
  fmt_ts = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
  fmt_flat = logging.Formatter("%(message)s")
  try:
      fh = logging.FileHandler(_log_file, encoding="utf-8")
  except OSError:
      log(f"[WARN] Could not create log file {_log_file}, falling back to stderr")
      fh = None
  ```

  然后在 line 159 的 `logger.addHandler(fh); logger.addHandler(ch)` 替换为：
  ```python
  if fh is not None:
      logger.addHandler(fh)
  logger.addHandler(ch)
  ```

  关键变更：
  - `_dir` 保留用于其他路径计算
  - 新增 `_log_dir` = `os.path.join(_dir, "logs")`
  - 新增 `os.makedirs(_log_dir, exist_ok=True)` 确保目录存在
  - 使用 `datetime.now()` 计算当天日期（需确保 `datetime` 已导入，当前代码 line 20 已有 `from datetime import datetime, timedelta, timezone`）
  - `_log_file` = `os.path.join(_log_dir, f"{_today}.log")`
  - FileHandler 创建包裹在 try/except 中，失败时 `fh = None`
  - 仅当 `fh is not None` 时才添加到 logger（回退到仅 stderr 输出）

- [ ] **Step 2: 验证语法正确**

  运行：
  ```bash
  python3 -c "import py_compile; py_compile.compile('x_monitor_nodriver.py', doraise=True)"
  ```
  Expected: 无输出（编译成功）

- [ ] **Step 3: Commit**

  ```bash
  git add x_monitor_nodriver.py
  git commit -m "refactor: move log file to logs/YYYYMMDD.log with auto-create dir"
  ```

---

## Task 2: 推文备份 — 新增 `_backup_tweets()` 函数

**Files:**
- Modify: `x_monitor_nodriver.py`（在 `BrowserSession` 类之后、`FeishuNotifier` 类之前插入新函数）

- [ ] **Step 1: 新增 `_backup_tweets()` 函数**

  在 `BrowserSession` 类结束（line 379）和 `FeishuNotifier` 类开始（line 381）之间插入：

  ```python
  def _backup_tweets(handle: str, tweets: list[Tweet]) -> None:
      """Backup fetched tweets as individual JSON files in backup/ directory."""
      _dir = os.path.dirname(os.path.abspath(__file__))
      _backup_dir = os.path.join(_dir, "backup")
      os.makedirs(_backup_dir, exist_ok=True)

      for tweet in tweets:
          _path = os.path.join(_backup_dir, f"{tweet.id}.json")
          if os.path.exists(_path):
              continue
          _data = {
              "id": tweet.id,
              "text": tweet.text,
              "link": tweet.link,
              "pubTime": tweet.pub_time,
              "handle": handle,
              "fetched_at": datetime.now(timezone.utc).isoformat(),
          }
          try:
              with open(_path, "w", encoding="utf-8") as f:
                  json.dump(_data, f, ensure_ascii=False, indent=2)
          except OSError as e:
              log(f"[BACKUP] Failed to write {tweet.id}: {e}")
  ```

  要点：
  - 使用模块级函数（非类方法），因为不需要访问类状态
  - `datetime.now(timezone.utc).isoformat()` 生成 ISO 8601 UTC 时间戳
  - `ensure_ascii=False` 保留中文等非 ASCII 字符
  - `indent=2` 便于人类阅读
  - 跳过已存在的文件（不覆盖）
  - 写入失败仅记录警告，不抛异常

- [ ] **Step 2: 验证语法正确**

  运行：
  ```bash
  python3 -c "import py_compile; py_compile.compile('x_monitor_nodriver.py', doraise=True)"
  ```
  Expected: 无输出（编译成功）

- [ ] **Step 3: Commit**

  ```bash
  git add x_monitor_nodriver.py
  git commit -m "feat: add _backup_tweets() to persist fetched tweets as JSON files"
  ```

---

## Task 3: 在 `fetch_tweets()` 中调用备份

**Files:**
- Modify: `x_monitor_nodriver.py`

- [ ] **Step 1: 在成功返回前调用 `_backup_tweets()`**

  在 `fetch_tweets()` 方法中，line 374-376（日志输出之后、return 之前）插入备份调用：

  当前代码：
  ```python
  log(f"[TWEETS] @{handle}: {len(tweets)} tweets, IDs: {[t.id for t in tweets]}")
  for i, t in enumerate(tweets):
      log(f"  [{i+1}] ID={t.id} | {t.text[:80]}")

  return FetchResult(tweets=tweets, status=FetchStatus.OK)
  ```

  替换为：
  ```python
  log(f"[TWEETS] @{handle}: {len(tweets)} tweets, IDs: {[t.id for t in tweets]}")
  for i, t in enumerate(tweets):
      log(f"  [{i+1}] ID={t.id} | {t.text[:80]}")

  _backup_tweets(handle, tweets)

  return FetchResult(tweets=tweets, status=FetchStatus.OK)
  ```

  注意：`handle` 参数已在 `fetch_tweets` 签名中定义，`tweets` 是 local variable。

- [ ] **Step 2: 验证语法正确**

  运行：
  ```bash
  python3 -c "import py_compile; py_compile.compile('x_monitor_nodriver.py', doraise=True)"
  ```
  Expected: 无输出（编译成功）

- [ ] **Step 3: Commit**

  ```bash
  git add x_monitor_nodriver.py
  git commit -m "feat: call _backup_tweets() after successful tweet extraction"
  ```

---

## Task 4: 更新 `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 添加 `backup/` 和 `logs/` 到 `.gitignore`**

  当前 `.gitignore` 内容：
  ```
  venv/*
  __pycache__/*
  config.ini
  cookies.json
  .claude/
  CLAUDE.md
  ```

  在 `cookies.json` 行之后添加：
  ```
  backup/
  logs/
  ```

  完整新内容：
  ```
  venv/*
  __pycache__/*
  config.ini
  cookies.json
  backup/
  logs/
  .claude/
  CLAUDE.md
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add .gitignore
  git commit -m "chore: ignore backup/ and logs/ directories"
  ```

---

## Task 5: 更新 CHANGELOG.md

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 在 `[Unreleased]` 下添加 v4.3 条目**

  在 `## [Unreleased]` 和 `## [4.2] - 2026-05-29` 之间插入：

  ```markdown
  ## [4.3] - 2026-05-29

  ### Added
  - **推文备份**：每次抓取到的推文按 ID 持久化为独立 JSON 文件，存放在 `backup/` 目录
  - **日志轮转**：日志从项目根目录迁移到 `logs/` 目录，按 `YYYYMMDD.log` 命名

  ### Changed
  - **BREAKING**：`x_monitor.log` 不再存放在项目根目录
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add CHANGELOG.md
  git commit -m "docs: add v4.3 changelog for tweet backup and log rotation"
  ```

---

## Task 6: 更新 README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新系统架构图中的日志部分**

  在架构图中添加 `logs/` 和 `backup/` 的展示。当前架构图 line 46-48：
  ```
                   ┌──────────────┐     ┌──────────────┐
                   │ cache.json   │     │ cookies.json │
                   │ (推文ID缓存)  │     │ (登录态注入)  │
                   └──────────────┘     └──────────────┘
  ```

  替换为：
  ```
                   ┌──────────────┐     ┌──────────────┐     ┌──────────┐
                   │ cache.json   │     │ cookies.json │     │ backup/  │
                   │ (推文ID缓存)  │     │ (登录态注入)  │     │ (推文备份) │
                   └──────────────┘     └──────────────┘     └──────────┘
                   ┌──────────────┐
                   │ logs/        │
                   │ (按日日志)    │
                   └──────────────┘
  ```

- [ ] **Step 2: 更新核心流程中的日志路径**

  在流程图中 line 79 附近，将：
  ```
  加载缓存 (cache.json) + 加载 cookies (cookies.json)
  ```
  替换为：
  ```
  加载缓存 (cache.json) + 加载 cookies (cookies.json)
  日志输出到 logs/YYYYMMDD.log
  ```

- [ ] **Step 3: 更新数据模型章节**

  在 line 169 的"浏览器会话"章节之后，新增"4.5 备份文件"和"4.6 日志文件"：

  ```markdown
  ### 4.5 备份文件 (backup/)

  每次成功抓取到的推文按 ID 存储为独立的 JSON 文件，存放在 `backup/` 目录下。

  每个文件包含：
  - `id`: 推文 ID（从 /status/ 提取）
  - `text`: 推文内容
  - `link`: 推文链接
  - `pubTime`: 发布时间（ISO 8601）
  - `handle`: 账号名
  - `fetched_at`: 抓取时间（ISO 8601 UTC）

  已存在的文件不会被覆盖。目录不存在时自动创建。

  ### 4.6 日志文件 (logs/)

  日志按日期轮转，存放在 `logs/` 目录下，每天一个 `YYYYMMDD.log` 文件。
  目录不存在时自动创建。写入失败时回退到 stderr 输出。
  ```

- [ ] **Step 4: 更新部署步骤**

  在 7.3 配置章节中，添加说明日志和备份目录：

  ```markdown
  ### 7.3 运行

  ```bash
  python3 x_monitor_nodriver.py
  ```

  > **注意**：脚本会自动检测 9222 端口状态，如未启动则自动启动 Xvfb 虚拟显示器和 Chrome 浏览器（含代理 `--proxy-server=http://127.0.0.1:20171`）。无需手动预先启动 Chrome。
  >
  > 抓取结果自动备份到 `backup/` 目录，日志自动轮转到 `logs/` 目录。
  ```

- [ ] **Step 5: 更新已知限制**

  在已知限制表格中新增一行：
  ```
  | 备份目录增长 | `backup/` 和 `logs/` 会随运行时间增长 | 可定期清理或归档 |
  ```

- [ ] **Step 6: 更新文档版本号**

  将文件末尾的：
  ```
  *文档版本: v4.2*
  *最后更新: 2026-05-29*
  *上次更新说明: Chrome 自动启动（Xvfb + nodriver），Cookie 注入时序修复，代理内置*
  ```
  替换为：
  ```
  *文档版本: v4.3*
  *最后更新: 2026-05-29*
  *上次更新说明: 推文按 ID 备份到 backup/，日志按日期轮转到 logs/*
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add README.md
  git commit -m "docs: update README for tweet backup and daily log rotation (v4.3)"
  ```

---

## Task 7: 端到端验证

**Files:**
- 无代码修改，仅运行验证

- [ ] **Step 1: 清理残留进程**

  ```bash
  pkill -f "Xvfb :99" 2>/dev/null; pkill -f "chrome.*9222" 2>/dev/null; sleep 1
  ```

- [ ] **Step 2: 运行脚本**

  ```bash
  timeout 120 python3 x_monitor_nodriver.py 2>&1
  ```

- [ ] **Step 3: 验证日志路径**

  ```bash
  ls -la logs/
  cat logs/$(date +%Y%m%d).log | head -20
  ```
  Expected: 存在 `logs/YYYYMMDD.log` 文件，包含 `[CHROME]`, `[COOKIE]`, `[TWEETS]` 等日志

- [ ] **Step 4: 验证备份文件**

  ```bash
  ls -la backup/
  cat backup/<tweet_id>.json
  ```
  Expected: 存在 `backup/<tweet_id>.json` 文件，包含 `id`, `text`, `link`, `pubTime`, `handle`, `fetched_at` 字段

- [ ] **Step 5: 验证项目根目录无日志**

  ```bash
  ls x_monitor.log 2>&1
  ```
  Expected: `No such file or directory`

- [ ] **Step 6: 验证备份不覆盖**

  再次运行脚本（应复用已有 Chrome 实例），检查备份文件是否被覆盖：
  ```bash
  stat backup/<tweet_id>.json | grep Modify
  ```
  Expected: Modify 时间不变（未覆盖）

- [ ] **Step 7: 提交并推送**

  ```bash
  git add -A
  git commit -m "chore: verify tweet backup and log rotation end-to-end"
  git push
  ```

---

## 自审 (Self-Review)

**1. Spec 覆盖：**
- 日志按日期轮转 → Task 1 (setup_logger), Task 7.3 (验证)
- 日志目录不存在时自动创建 → Task 1 (os.makedirs)
- 跨天运行时自动切换 → Task 1 (datetime.now() 每次启动重新计算)
- 日志写入失败回退 → 当前未实现，需补充
- 项目根目录不再存放日志 → Task 1 (路径变更), Task 7.5 (验证)
- 推文按 ID 持久化 → Task 2 (_backup_tweets), Task 7.4 (验证)
- 已存在时不覆盖 → Task 2 (os.path.exists check)
- 备份目录不存在时自动创建 → Task 2 (os.makedirs)
- 备份失败不阻塞 → Task 2 (try/except)
- 备份文件包含 handle 和 fetched_at → Task 2 (_data dict)

**2. 缺失项：日志写入失败回退到 stderr**

当前 `_setup_logger()` 直接创建 FileHandler，如果 `logs/` 目录写入失败会抛异常。需要添加 fallback：

在 Task 1 的 Step 1 中，将 FileHandler 创建改为：
```python
try:
    fh = logging.FileHandler(_log_file, encoding="utf-8")
except OSError:
    log(f"[WARN] Could not create log file {_log_file}, falling back to stderr")
    fh = None
```
然后在 `logger.addHandler(fh)` 前加：
```python
if fh is not None:
    logger.addHandler(fh)
```

**3. 类型一致性：**
- `datetime` 已在 line 20 导入 ✓
- `json` 已在 line 12 导入 ✓
- `os` 已在 line 14 导入 ✓
- `handle` 参数在 `fetch_tweets` 中已定义 ✓
- `tweets` 是 local variable ✓

**4. 无 placeholder：**
- 所有步骤包含完整代码 ✓
- 所有步骤包含具体命令 ✓
- 所有步骤包含预期输出 ✓
