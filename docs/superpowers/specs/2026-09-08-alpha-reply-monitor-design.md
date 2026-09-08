# Alpha 推文回复监控 — 设计文档

日期：2026-09-08
状态：已获用户口头确认，待审阅
关联：`x_monitor_nodriver.py`（v4.4）、README v4.4、CHANGELOG

## 1. 背景与目标

现有监控只抓 `x.com/{handle}` 主页时间线。X 的主页时间线默认**不展示**该账号在自己帖子下的追加回复（thread 楼内续推），导致这类内容漏推。

真实案例：主推文 `x.com/binancezh/status/2096515652756599147` 已监控到，但其楼内回复未推送。

目标：**仅**监控「7 天内 Alpha 类推文」下、由**监控账号本人**发出的自回复，命中后合并进现有飞书新帖卡片（标题带 ALPHA，通知预览直接可辨）。

### 范围界定（已与用户确认）

| 场景 | 是否监控 |
|------|---------|
| A 回复 A 自己的 Alpha 帖（thread 楼内续推） | ✅ 要 |
| B 回复 B 自己的 Alpha 帖 | ✅ 要 |
| A 回复 B 的帖子（跨账号互回） | ❌ 不要 |
| 任何账号回复非 Alpha 帖 / 超过 7 天的帖 | ❌ 不要 |
| 第三方用户回复监控账号的帖子 | ❌ 不要 |

典型价值场景：@BinanceWallet 常以「主推文 + 自己回复补链接」形式发布公告（如 `Access Prediction Market via👇`、`👉 Agentic Wallet Skill: https://…`），正文关键信息在楼内。

## 2. 探测结论（一次性脚本实测，脚本不进仓库）

对 @binancezh、@binancewallet 的 `x.com/{handle}/with_replies` 页面实测：

1. **回复量极低**：@binancezh 全部约 13 条 article（覆盖 3 周）、@binancewallet 约 11 条。30 分钟一轮的抓取窗口无挤占风险。
2. **X 不为自回复渲染「回复 @xxx」chip**——DOM `data-testid` 列表中无回复上下文元素，原计划的文本正则提取方案作废。
3. ~~父帖链接是天然判据~~ **实施时二次证伪（2026-09-08）**：自回复 article 内除自身链接外只有 `/analytics` 等指向自身的伪链接，**DOM 完全拿不到父帖**；跨账号回复才有父帖 chip。父帖信息改由 **syndication 公开接口** `cdn.syndication.twimg.com/tweet-result?id=…&token=…` 逐候选查询 `in_reply_to_status_id_str` / `in_reply_to_screen_name`（实测可用，走现有代理；`_syndication_token` 复刻 JS 表达式）。水位只越过「连续已解析前缀」，查询失败的候选留待下轮，防吞。
4. **with_replies 页面混入他人 article**（X 塞入的对话上下文，如 @Xiaoyu_184CM、@MissLulu016 出现在 @binancezh 页面），必须按 handle 过滤，不能假设页面内所有推文都属于该 handle。

## 3. 架构与数据流

```
每轮监控（现有 Monitor 循环内追加，不新增进程）:

  ① 构建「活跃 Alpha 父帖集合」
     扫描 backup/*.json（现有备份：id/handle/text/pubTime）
     过滤：classify_alpha(text) 命中 且 pubTime 距今 ≤ ALPHA_REPLY_WINDOW_DAYS
     → { parent_id: (handle, text, pub_time) }   # 典型规模 2~8 条

  ② 集合为空 → 本轮跳过回复抓取（零额外页面加载）
     否则按 handle 分组，仅对涉及的 handle 执行：

  ③ fetch_replies(handle):
     导航 x.com/{handle}/with_replies
     复用现有 tab 复用 / cookie 注入 / 登录页重定向检测逻辑
     滚动 2 次（每次 ~2000px，间隔 1.2s）触发懒加载
     提取 JS 取顶部最多 20 条 article
     REPLY_EXTRACT_JS 提取每条 article：
       self_handle, self_id   ← 时间戳链接路径
       parent_handle, parent_id ← 其余非 blockquote 的 /status/ 链接（取第一个）
       text, pubTime          ← 同现有逻辑
     Python 侧入选过滤（全部条件须成立）：
       a. parent_id ∈ Alpha 父帖集合
       b. self_handle == 该 handle（滤掉混入的他人推文）
       c. parent_handle == self_handle（自回复：A 回 A；排除跨账号互回）
       d. self_id > 水位 {handle}:replies（去重，见 §5）
       e. backup/<self_id>.json 不存在（幂等保险，见 §5）

  ④ 新回复并入该账号的推送数据 → 现有卡片渲染（见 §4）
```

## 4. 推送呈现（合并进现有交互卡片）

- 回复条目排在其所属账号发帖条目之后，区块格式：
  `🧵 ALPHA 帖新回复` + 回复正文 + `[↩️ 查看被回复原帖](父帖链接)` + `[🔗 查看回复]`
- 父帖必为 Alpha → 回复条目**一律带 ALPHA 横幅**，并计入 `has_alpha`；整卡标题为「🔴 ALPHA｜X 新帖提醒」，header 红色。
- 本轮只有新回复、无新发帖时，**同样推卡**（该账号区块仅含回复条目）。
- `send_expired` 等其它卡片不受影响。

## 5. 数据模型：去重与缓存

### cache.json

新增独立键，与主时间线键互不干扰（避免互相吞帖）：

```json
{
  "binancezh": "...",
  "binancezh:replies": "2096871440704561524",
  "binancewallet": "...",
  "binancewallet:replies": "..."
}
```

- 语义：该 handle 已推送的最大回复推文 ID（X 推文 ID 为时间有序的 snowflake，可直接数值比较）。
- **首次运行**（键不存在）：不做静默种子，**命中即推**（修订记录：原设计"只记水位不推送"存在缝隙——水位仅在推送成功轮次落盘，种子轮与新推送落盘之间到达的回复会被反复重种子吞掉；父帖 7 天 Alpha 过滤本身已压制历史噪音，最坏首跑推送量为窗口内活跃楼内回复，≤7 天且封顶 5 条/账号）。本轮无命中则不写键。
- 后续轮次：`self_id > 水位` 入选；推送后水位前移为本轮入选的最大 ID（若无入选则不动）。

### backup/

- 回复推文与发帖推文同等对待，落盘 `backup/<id>.json`（现有 `_backup_tweets` 直接复用，字段含 parent_link 的扩展见 §6）。
- ~~「backup 文件已存在」作第二重去重保险~~（实施时弃用：回复在选中时即备份，若推送失败水位不落盘，backup 存在检查会导致永久漏推；去重与发帖一致，仅用水位）。

## 6. 组件清单

| 单元 | 形态 | 职责 | 依赖 |
|------|------|------|------|
| `find_alpha_parents(now, backup_dir)` | 新增纯函数 | 扫备份 → 7 天内 Alpha 推文 dict | `classify_alpha`、backup/*.json |
| `ALPHA_REPLY_WINDOW_DAYS = 7` | 新增常量 | 回复监控时间窗 | — |
| `REPLY_EXTRACT_JS` | 新增类常量 | 提取 self/parent 双链接 + 文本 + 时间 | 探测结论 §2 |
| `BrowserSession.fetch_replies(handle, config)` | 新增 classmethod | 导航/滚动/提取，返回 `FetchResult[list[Reply]]` | `_get_browser`、`_inject_cookies` |
| `Reply` | 新增 dataclass | `id, text, link, pub_time, parent_id, parent_link` | — |
| `AccountResult` | 修改 | 增 `replies: list[Reply]` 字段 | — |
| `Monitor.run` | 修改 | 发帖抓取后追加回复步骤 | `find_alpha_parents`、`fetch_replies` |
| `_build_new_tweets_card` | 修改 | 渲染回复区块、横幅、标题逻辑 | `Reply` |

Tweet 的备份函数增加可选 `parent_link` 字段（仅回复条目有值），旧备份文件无该字段读取处需容错。

## 7. 错误处理

| 故障 | 行为 |
|------|------|
| fetch_replies 超时 / JS 异常 | `log("[FAIL][REPLIES] ...")` 后继续，不影响发帖推送；**不重试**（回复非关键路径，下轮自然补上，水位保证不漏推） |
| 重定向到登录页 | 走现有 `EXPIRED` → `send_expired` 逻辑（同主页抓取） |
| backup 单文件损坏 / 字段缺失 | `find_alpha_parents` 跳过该文件继续扫描 |
| 无 x.com 登录 tab 且 Chrome 冷启动 | 复用 `fetch_tweets` 相同的启动路径（同端口同 profile，不会起两个浏览器） |
| 单轮回复过多（异常爆刷） | 每账号每轮最多推 5 条，超出部分丢弃并照常前移水位（防刷屏优先于全量） |

## 8. 测试策略

### 单元测试（pytest，扩展 test_x_monitor.py）

1. `find_alpha_parents`：7 天边界（第 7 天内/外）；非 Alpha 排除；引用推文文本误分类不干扰；损坏 JSON 容忍；空目录。
2. 回复入选过滤：父帖 ID 匹配/handle 不匹配混入他人推文/跨账号回复（A 回 B）排除/blockquote 引用链接干扰。
3. 卡片构建：含回复条目 → 标题带 ALPHA + 红色 + `🧵` 标记 + 原帖链接；只有回复无发帖 → 仍出卡；回复与发帖混合排序。
4. 水位：首跑只记不推；ID ≤ 水位跳过；推送后前移。
5. `Reply` dataclass 与备份 round-trip（含/不含 parent_link 字段）。

### 真机验证

- 构造历史回放：临时清空 `{handle}:replies` 水位，跑一轮端到端，确认飞书卡片渲染与 backup 落盘。
- 等待自然产生一条新 Alpha 楼内回复，确认 ≤30 分钟内收到推送。

## 9. 已知限制与取舍

- 仅覆盖「本脚本已抓到并备份过」的 Alpha 主推文（backup/ 是父帖唯一数据源）。脚本停摆期间发的 Alpha 帖，其回复不会被监控。
- with_replies 页面只取顶部 20 条 article（含混入的他人帖），按实测回复量（2~3 天/条）远未触顶；若未来新增高频回复类账号（客服号），需调大窗口或加分页。
- 水位为每 handle 单值；若回复 ID 恰好与发帖共用号段无冲突问题（X snowflake 全局单调）。
- `to:`/`from:` 搜索方案被否决：搜索页反爬更强、长楼漏匹配（见会话方案对比）。

## 10. 不做清单（YAGNI）

- 不做回复的回复（楼中楼第三层及以下）监控——只取第一层自回复；X DOM 上二者同样可提取，但需求未提出。
- 不做 config.ini 新配置项（窗口天数、条数上限均为常量）。
- 不做跨账号互回（A 回 B）通知——用户明确排除。
- 不做回复的历史回溯推送（首跑静默建水位即可）。
