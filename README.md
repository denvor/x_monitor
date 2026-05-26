# X-Monitor 产品方案

> 币安相关 X 账号推文监控与飞书推送

---

## 1. 产品概述

### 1.1 目标

实时监控币安（Binance）相关 X（Twitter）账号的新推文，发现新帖后通过飞书（Feishu）即时推送通知用户。

### 1.2 核心价值

- **时效性**：每 30 分钟检查一次（08:00-22:30），新帖 30 分钟内推送
- **低噪音**：基于推文 ID 去重，每条推文只推一次
- **自动化**：全自动运行，无需人工干预
- **Cookie 注入**：通过 CDP 注入 cookie 绕过 nodriver 检测，确保 X 正常认证
- **智能推送**：首次运行推送最近 3 条历史推文，后续只推送新增推文（最多 3 条）

### 1.3 监控账号

| 账号 | 说明 |
|------|------|
| @binancezh | 币安中文官方账号 |
| @binancewallet | 币安钱包官方账号 |

### 1.4 运行周期

- **频率**：每 30 分钟
- **时段**：08:00 - 22:30（北京时间，每天）
- **触发方式**：Hermes Agent Cron 定时任务

---

## 2. 系统架构

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│  Cron 调度器 │ ──▶ │  x-monitor   │ ──▶ │  nodriver    │ ──▶ │  x.com   │
│  (每30分钟)  │     │  主脚本      │     │  Chrome 接管  │     │  (X)     │
│  Hermes Agent│     │  (Python)    │     │  :9222        │     │          │
└─────────────┘     └──────┬───────┘     └──────────────┘     └──────────┘
                           │
                   ┌──────────────┐     ┌──────────────┐
                   │ cache.json   │     │ cookies.json │
                   │ (推文ID缓存)  │     │ (登录态注入)  │
                   └──────────────┘     └──────────────┘
```

### 2.1 消息投递机制

脚本通过 Feishu REST API 直接发送通知消息到飞书群聊，包含 token 缓存机制。

```
脚本 ──▶ Feishu API (tenant_access_token) ──▶ 飞书群聊
```

### 2.2 与 v1 的架构差异

| 组件 | v1 (curl_cffi) | v2 (nodriver) |
|------|----------------|---------------|
| 请求方式 | HTTP POST + TLS 指纹伪装 | 接管已登录 Chrome 浏览器 |
| 登录态 | cookies.json 手动注入 | CDP 注入 cookie + 浏览器自管理 |
| 解析方式 | HTML 正则 + 嵌入式 JSON | JS evaluate + DOM query |
| Cloudflare | 可能触发拦截 | 不会（真实浏览器） |
| Cookie 文件 | 需要维护 cookies.json | 需要 cookies.json（首次打开页面时注入） |

---

## 3. 核心流程

### 3.1 完整流程图

```
开始
 │
 ▼
加载缓存 (cache.json) + 加载 cookies (cookies.json)
 │
 ▼
遍历监控账号列表 [binancezh, binancewallet]
 │
 ├─▶ nodriver 接管 Chrome（127.0.0.1:9222）
 │     │
 │     ├─ 复用已有浏览器实例，不启动新窗口
 │     │
 │     ├─ 未找到 x.com tab → 打开新页面 + CDP 注入 cookies
 │     │
 │     ├─ 导航至 x.com/{handle}
 │     │
 │     ├─ 等待推文元素出现（article / tweetText，30s）
 │     │
 │     ├─ 提取推文列表（JS evaluate）
 │     │   ├─ 跳过置顶推文（UserPin / tweetWithIntentHeader）
 │     │   ├─ 取前 3 条普通推文
 │     │   ├─ 推文 ID（从 /status/ 提取）
 │     │   ├─ 推文内容
 │     │   ├─ 推文链接
 │     │   └─ 发布时间（<time datetime="...">）
 │     │
 │     └─ 已有 x.com tab → 复用，不重新注入 cookie
 │
 ├─▶ 重试机制（最多 3 次，间隔 3 秒）
 │
 ├─▶ 对比缓存 ID（数值比较）
 │   ├─ 首次运行 → 推送最近 3 条
 │   ├─ 有新推文 → 推送新增推文（最多 3 条）
 │   └─ 无新推文 → 跳过
 │
 ├─▶ 飞书 API 发送通知
 │
 ├─▶ 更新缓存
 │
 ▼
遍历下一个账号（或结束）
 │
 ▼
页面重定向到登录页 → 发送过期通知
 │
 ▼
结束
```

### 3.2 关键参数

| 参数 | 当前值 | 说明 |
|------|--------|------|
| 页面等待超时 | 30s | article 选择器 + tweetText 回退 |
| 抓取上限 | 3 条 | 每次抓取 3 条非置顶推文 |
| 推送上限 | 3 条 | 每次最多推送 3 条推文 |
| MAX_RETRIES | 2 | 单账号最大重试次数（共 3 次尝试） |
| RETRY_DELAY | 3s | 重试间隔 |
| 置顶过滤 | 全部检查 | 过滤 UserPin / tweetWithIntentHeader |

---

## 4. 数据模型

### 4.1 缓存文件 (cache.json)

存储每个账号最近抓到的最大推文 ID，用于去重检测。

```json
{
  "binancezh": "2054474363454541889",
  "binancewallet": "2054176242380034129"
}
```

### 4.2 Cookie 文件 (cookies.json)

Firefox 格式的 cookie 导出文件，包含 x.com 的登录态 cookie。通过 CDP `Network.setCookies` 注入到浏览器。

- **敏感文件**，已加入 `.gitignore`，不提交到仓库
- 使用 `cookies.json.sample` 作为模板填充实际值

### 4.3 日志文件 (x_monitor.log)

结构化日志，包含：
- `[COOKIE]` - cookie 注入状态
- `[EXPIRED]` - 页面重定向到登录页
- `[TWEETS]` - 推文抓取结果
- `[DEDUP]` - 去重统计
- `[FEISHU]` - 飞书推送状态
- `[FAIL]` - 抓取失败
- 运行状态和时间戳

### 4.4 浏览器会话

- 通过 `nodriver` 接管 `127.0.0.1:9222` 上已运行的 Chrome
- 未找到已有 x.com tab 时，通过 CDP 注入 cookies.json 中的 cookie
- X 会检测 nodriver 启动的浏览器并阻止登录，cookie 注入可绕过此检测
- 使用前需确保 Chrome 已通过 `--remote-debugging-port=9222` 启动并登录 X

---

## 5. 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 浏览器自动化 | nodriver (async) | 接管已有 Chrome 实例 |
| CDP 协议 | nodriver.cdp.network | Cookie 注入（Network.setCookies） |
| 消息推送 | Feishu REST API | 直接调用飞书 API，含 token 缓存 |
| 调度器 | Hermes Agent Cron | 系统级定时任务 |
| 日志 | Python logging | 文件 + 控制台双输出 |
| 异步运行时 | asyncio | `asyncio.run()` |

---

## 6. 文件清单

| 文件 | 用途 |
|------|------|
| `x_monitor_nodriver.py` | 主脚本 |
| `x_nodriver.py` | 独立抓取测试脚本 |
| `config.ini` | 配置文件（飞书凭证、监控账号等） |
| `cache.json` | 推文 ID 缓存 |
| `cookies.json` | Cookie 文件（登录态，不在仓库中） |
| `cookies.json.sample` | Cookie 文件模板 |
| `x_monitor.log` | 运行日志 |

---

## 7. 部署步骤

### 7.1 前置条件

- Python 3.12+
- Google Chrome 已安装
- nodriver 已安装：`pip install nodriver`

### 7.2 启动 Chrome（调试模式）

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug --no-sandbox --disable-gpu --proxy-server="http://127.0.0.1:20171"
```

> **注意**：Chrome 需单独启动，确保已登录 X 账号。`--user-data-dir` 指向独立目录，不影响日常浏览器。`--proxy-server` 指定代理，不依赖系统代理设置。

### 7.3 配置

1. 复制 `cookies.json.sample` 为 `cookies.json`，填入实际的 cookie 值（从浏览器开发者工具获取）
2. 编辑 `config.ini`，填入飞书 app_id、app_secret、chat_id 等信息

### 7.4 运行

```bash
python3 x_monitor_nodriver.py
```

---

## 8. 当前状态

### 8.1 已完成

- ✅ 核心抓取逻辑（nodriver + JS evaluate）
- ✅ 接管已有 Chrome（127.0.0.1:9222）
- ✅ Cookie 注入（CDP Network.setCookies，绕过 nodriver 检测）
- ✅ 推文去重（数值 ID 比较）
- ✅ 置顶推文过滤（UserPin / tweetWithIntentHeader）
- ✅ 重试机制（3 次尝试，3 秒间隔）
- ✅ 结构化日志（文件 + 控制台）
- ✅ 飞书消息推送（REST API + token 缓存）
- ✅ 推文时间显示（从 `<time datetime="...">` 读取，北京时间）
- ✅ 首次运行推送历史推文（最近 3 条）

### 8.2 运行状态

- Cron 任务：`last_status: "ok"`，`next_run_at` 由调度器管理
- Chrome：需通过 `google-chrome --remote-debugging-port=9222` 启动并登录 X，需指定 `--proxy-server` 代理

---

## 9. 已知限制

| 限制 | 说明 | 影响 |
|------|------|------|
| 仅抓取主页时间线 | 不进入单条推文详情页 | 无法获取评论/转发数 |
| 最多 3 条 | 每次只抓取并推送 3 条非置顶推文 | 如果 30 分钟内超过 3 条，部分推文可能跳过 |
| 依赖 Chrome 会话 | 需要已登录的 Chrome 实例运行在 :9222 | Chrome 未启动或登出则无法抓取 |
| X 反爬风险 | X 可能变更 DOM 结构或反爬策略 | 选择器可能失效 |
| Cookie 过期 | cookies.json 中的 cookie 会过期 | 需定期从浏览器更新 cookie |

---

## 10. 风险与依赖

| 风险 | 影响 | 缓解 |
|------|------|------|
| X 反爬策略变更 | 选择器失效，无法抓取 | 多选择器回退 + 定期维护 |
| Chrome 会话断开 | 无法连接 :9222 | 自动重试 + 日志记录 |
| 推送失败 | 用户收不到通知 | 日志记录 + 脚本崩溃时输出 traceback |
| Cookie 泄露 | cookies.json 包含敏感登录态 | 已加入 .gitignore，不提交到仓库 |

---

*文档版本: v4.1*
*最后更新: 2026-05-26*
*上次更新说明: 添加 Chrome 代理配置（--proxy-server），修复推文提取重复问题（JS 端 link 去重）*
