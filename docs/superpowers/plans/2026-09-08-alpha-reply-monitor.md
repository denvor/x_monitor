# Alpha 推文回复监控 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 监控监控账号在自己 7 天内 Alpha 类推文下的楼内自回复，合并进现有飞书交互卡片推送（标题带 ALPHA）。

**Architecture:** 在现有 `x_monitor_nodriver.py` 单文件内追加：扫描 `backup/` 构建「活跃 Alpha 父帖集合」→ 对有 Alpha 父帖的 handle 抓取 `x.com/{handle}/with_replies`（DOM 结构与主页时间线一致，父帖关系由 article 内非时间戳的 /status/ 链接判定）→ Python 侧纯函数过滤新回复 → 合并进现有卡片。去重采用与发帖一致的「每源独立最大 ID 水位」模式，缓存键 `{handle}:replies`。

**Tech Stack:** Python 3.12 标准库（无新依赖）、nodriver（已有）、pytest（`/home/denvor/.local/bin/pytest`）。

**Spec:** `docs/superpowers/specs/2026-09-08-alpha-reply-monitor-design.md`

## Global Constraints

- 代码注释用中文；对话产出文档中文
- 不新增第三方依赖
- 测试命令统一为：`/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -v`（hermes venv 无 pytest/nodriver，勿用）
- 不提交 `config.ini`、`cookies.json`（含真实凭证）
- 时间窗常量 `ALPHA_REPLY_WINDOW_DAYS = 7`；单轮单账号回复推送上限 `MAX_REPLIES_PER_PUSH = 5`
- 去重仅用水位（**对 spec §5「backup 已存在」双保险的一处有意偏离**：回复在选中时即备份，若推送失败水位不落盘，backup 存在检查会导致该回复永久漏推；与发帖去重保持同一水位模式）

## File Structure

全部改动在两个既有文件（单文件脚本是现有形态，遵循不做拆分重构）：

| 文件 | 职责变化 |
|------|---------|
| `x_monitor_nodriver.py` | 新增：常量 2 个、`Reply` 数据类、`find_alpha_parents()`、`select_new_replies()`、`REPLY_EXTRACT_JS`、`BrowserSession.fetch_replies()`；修改：`_backup_tweets()`（parent_link）、`AccountResult`（+replies）、`_build_new_tweets_card()`（回复区块）、`Monitor.run()`（接线）、主入口超时 120→240 |
| `test_x_monitor.py` | 新增 5 个测试类（本计划所有单测） |
| `README.md` / `CHANGELOG.md` | Task 7 文档收尾 |

---

### Task 1: `find_alpha_parents` — Alpha 父帖集合（纯函数）

**Files:**
- Modify: `x_monitor_nodriver.py`（在 `classify_alpha` 函数之后、`_parse_proxy` 之前插入）
- Test: `test_x_monitor.py`（文件末尾追加）

**Interfaces:**
- Consumes: 既有 `classify_alpha(text) -> Optional[AlphaCategory]`、`log()`
- Produces: `ALPHA_REPLY_WINDOW_DAYS: int = 7`；`find_alpha_parents(backup_dir: str, now: datetime) -> dict[str, tuple[str, str]]`，值 = `(handle, text)`，key = 推文 ID 字符串

- [ ] **Step 1: 写失败测试**

在 `test_x_monitor.py` 末尾追加（注意顶部 import 处需补 `import json`）：

```python
# ── find_alpha_parents ──────────────────────────────────────────────

from x_monitor_nodriver import ALPHA_REPLY_WINDOW_DAYS, find_alpha_parents


class TestFindAlphaParents:
    ALPHA_TEXT = "币安 Alpha 将在 8 月 26 日成为首个上线 Teller（DEBIT）的平台！"
    PLAIN_TEXT = "币安发布季度报告，业绩创新高。"

    def _mk(self, tmp_path, tid, handle, text, pub_dt):
        data = {"id": tid, "handle": handle, "text": text,
                "link": f"https://x.com/{handle}/status/{tid}",
                "pubTime": pub_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        (tmp_path / f"{tid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_only_alpha_within_window_selected(self, tmp_path):
        now = datetime.now(timezone.utc)
        self._mk(tmp_path, "100", "binancezh", self.ALPHA_TEXT, now - timedelta(days=1))
        self._mk(tmp_path, "101", "binancezh", self.PLAIN_TEXT, now - timedelta(days=1))
        self._mk(tmp_path, "102", "binancezh", self.ALPHA_TEXT, now - timedelta(days=8))
        parents = find_alpha_parents(str(tmp_path), now)
        assert set(parents.keys()) == {"100"}
        assert parents["100"][0] == "binancezh"

    def test_window_boundary(self, tmp_path):
        now = datetime.now(timezone.utc)
        self._mk(tmp_path, "200", "binancezh", self.ALPHA_TEXT, now - timedelta(days=ALPHA_REPLY_WINDOW_DAYS, minutes=1))
        self._mk(tmp_path, "201", "binancezh", self.ALPHA_TEXT, now - timedelta(days=ALPHA_REPLY_WINDOW_DAYS - 0.01))
        parents = find_alpha_parents(str(tmp_path), now)
        assert "200" not in parents and "201" in parents

    def test_corrupt_and_foreign_files_tolerated(self, tmp_path):
        now = datetime.now(timezone.utc)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "note.txt").write_text("ignore", encoding="utf-8")
        self._mk(tmp_path, "300", "binancezh", self.ALPHA_TEXT, now)
        parents = find_alpha_parents(str(tmp_path), now)
        assert set(parents.keys()) == {"300"}

    def test_missing_dir_returns_empty(self, tmp_path):
        assert find_alpha_parents(str(tmp_path / "nope"), datetime.now(timezone.utc)) == {}
```

顶部 import 区（第 3-6 行附近）补：`from datetime import datetime, timedelta, timezone` 与 `import json`。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -k FindAlphaParents -v`
Expected: ImportError / `find_alpha_parents` 未定义

- [ ] **Step 3: 最小实现**

在 `x_monitor_nodriver.py` 的 `classify_alpha` 之后插入：

```python
# ── Alpha 楼内回复监控：父帖集合 ─────────────────────────────────────

ALPHA_REPLY_WINDOW_DAYS = 7   # 只监控 7 天内 Alpha 推文下的回复
MAX_REPLIES_PER_PUSH = 5      # 单账号单轮回复推送上限（防刷屏）


def find_alpha_parents(backup_dir: str, now: datetime) -> dict[str, tuple[str, str]]:
    """扫描备份目录，返回 7 天内 Alpha 类推文集合。

    返回 {推文ID: (handle, 正文)}；损坏文件 / 缺字段文件静默跳过。
    """
    parents: dict[str, tuple[str, str]] = {}
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return parents
    cutoff = now - timedelta(days=ALPHA_REPLY_WINDOW_DAYS)
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(backup_dir, name), "r", encoding="utf-8") as f:
                data = json.load(f)
            pub = datetime.fromisoformat(str(data["pubTime"]).replace("Z", "+00:00"))
            if pub < cutoff:
                continue
            if classify_alpha(data.get("text", "")) is None:
                continue
            parents[str(data["id"])] = (data.get("handle", ""), data.get("text", ""))
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return parents
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -k FindAlphaParents -v`
Expected: 4 passed。随后全量跑一次确认无回归：`... test_x_monitor.py -v`（28 passed）

- [ ] **Step 5: Commit**

```bash
git add x_monitor_nodriver.py test_x_monitor.py
git commit -m "feat: find_alpha_parents — 扫描备份构建 7 天内 Alpha 父帖集合"
```

---

### Task 2: `Reply` 数据类 + 备份支持 parent_link

**Files:**
- Modify: `x_monitor_nodriver.py`（`AccountResult` 定义处、`_backup_tweets`）
- Test: `test_x_monitor.py`

**Interfaces:**
- Consumes: 既有 `Tweet`（字段 id/text/link/pub_time，属性 id_numeric/beijing_time）
- Produces: `Reply(Tweet)`，新增字段 `parent_id: str = ""`、`parent_link: str = ""`；`AccountResult.replies: list[Reply]`；`_backup_tweets` 对带非空 `parent_link` 的对象在 JSON 中写入 `"parent_link"` 字段

- [ ] **Step 1: 写失败测试**

```python
# ── Reply 数据类与备份 ───────────────────────────────────────────────

from x_monitor_nodriver import Reply, _backup_tweets


class TestReplyModel:
    def _reply(self, tid="900"):
        return Reply(id=tid, text="👉 领取链接", link=f"https://x.com/binancezh/status/{tid}",
                     pub_time="2026-09-01T08:00:00.000Z",
                     parent_id="100", parent_link="https://x.com/binancezh/status/100")

    def test_reply_is_tweet_and_carries_parent(self):
        r = self._reply()
        assert isinstance(r, Tweet)
        assert r.id_numeric == 900 and r.parent_id == "100"

    def test_account_result_replies_default_empty(self):
        a = AccountResult(handle="binancezh", tweets=[])
        assert a.replies == []

    def test_backup_writes_parent_link_for_replies(self, tmp_path, monkeypatch):
        monkeypatch.setattr("x_monitor_nodriver._SCRIPT_DIR", str(tmp_path))
        _backup_tweets("binancezh", [self._reply()])
        saved = json.loads((tmp_path / "backup" / "900.json").read_text(encoding="utf-8"))
        assert saved["parent_link"] == "https://x.com/binancezh/status/100"

    def test_backup_unchanged_for_plain_tweets(self, tmp_path, monkeypatch):
        monkeypatch.setattr("x_monitor_nodriver._SCRIPT_DIR", str(tmp_path))
        _backup_tweets("binancezh", [Tweet(id="901", text="hi", link="l", pub_time="")])
        saved = json.loads((tmp_path / "backup" / "901.json").read_text(encoding="utf-8"))
        assert "parent_link" not in saved
```

（测试文件顶部 import 已含 `Tweet`、`AccountResult`；`_backup_tweets` 现成。）

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -k TestReplyModel -v`
Expected: ImportError `Reply`

- [ ] **Step 3: 实现**

`AccountResult` 定义改为（在既有字段基础上加一行）：

```python
@dataclass
class Reply(Tweet):
    """Alpha 推文下的楼内自回复（回复者 == 被回复帖作者）。"""
    parent_id: str = ""
    parent_link: str = ""


@dataclass
class AccountResult:
    handle: str
    tweets: list[Tweet] = field(default_factory=list)
    replies: list[Reply] = field(default_factory=list)  # Alpha 帖楼内新回复
    status: FetchStatus = FetchStatus.OK  # overall result for this account
```

`_backup_tweets` 中 `_data = {...}` 之后加两行：

```python
        if getattr(tweet, "parent_link", ""):
            _data["parent_link"] = tweet.parent_link
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -v`
Expected: 全量 32 passed

- [ ] **Step 5: Commit**

```bash
git add x_monitor_nodriver.py test_x_monitor.py
git commit -m "feat: Reply 数据类 + AccountResult.replies + 备份支持 parent_link"
```

---

### Task 3: `select_new_replies` — 回复入选过滤（纯函数）

**Files:**
- Modify: `x_monitor_nodriver.py`（`find_alpha_parents` 之后）
- Test: `test_x_monitor.py`

**Interfaces:**
- Consumes: `Reply`、Task 1 的 parents 结构
- Produces: `select_new_replies(rows: list[dict], handle: str, parents: dict[str, tuple[str, str]], watermark: Optional[int]) -> list[Reply]`——rows 为 JS 提取结果，字段：`selfLink, selfHandle, selfId, parentLink, parentHandle, parentId, text, pubTime`；返回按 ID 升序

- [ ] **Step 1: 写失败测试**

```python
# ── select_new_replies ───────────────────────────────────────────────

from x_monitor_nodriver import select_new_replies


class TestSelectNewReplies:
    P = {"100": ("binancezh", "Alpha 主帖")}

    def _row(self, **kw):
        base = {"selfLink": "https://x.com/binancezh/status/200", "selfHandle": "binancezh",
                "selfId": "200", "parentLink": "https://x.com/binancezh/status/100",
                "parentHandle": "binancezh", "parentId": "100",
                "text": "👉 补充链接", "pubTime": "2026-09-01T08:00:00.000Z"}
        base.update(kw)
        return base

    def test_self_reply_to_alpha_parent_selected(self):
        out = select_new_replies([self._row()], "binancezh", self.P, watermark=100)
        assert [r.id for r in out] == ["200"]
        assert out[0].parent_link.endswith("/100")

    def test_foreign_author_filtered(self):
        # with_replies 页混入他人推文（selfHandle != handle）
        out = select_new_replies([self._row(selfHandle="SomeUser", selfId="201")], "binancezh", self.P, 100)
        assert out == []

    def test_cross_account_reply_filtered(self):
        # A 回 B：parentHandle != handle
        out = select_new_replies([self._row(parentHandle="binancewallet", parentId="100")], "binancezh", self.P, 100)
        assert out == []

    def test_non_alpha_parent_filtered(self):
        out = select_new_replies([self._row(parentId="999")], "binancezh", self.P, 100)
        assert out == []

    def test_watermark_dedup_and_none_means_all(self):
        rows = [self._row(selfId="200"), self._row(selfId="300", parentLink="p3", parentId="100")]
        assert [r.id for r in select_new_replies(rows, "binancezh", self.P, 200)] == ["300"]
        assert len(select_new_replies(rows, "binancezh", self.P, None)) == 2

    def test_duplicate_selfid_dropped_and_sorted(self):
        rows = [self._row(selfId="300"), self._row(selfId="300"), self._row(selfId="200")]
        out = select_new_replies(rows, "binancezh", self.P, None)
        assert [r.id for r in out] == ["200", "300"]

    def test_missing_selfid_tolerated(self):
        out = select_new_replies([self._row(selfId="", selfLink="")], "binancezh", self.P, None)
        assert out == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -k SelectNewReplies -v`
Expected: ImportError `select_new_replies`

- [ ] **Step 3: 实现**

```python
def select_new_replies(rows: list[dict], handle: str,
                       parents: dict[str, tuple[str, str]],
                       watermark: Optional[int]) -> list[Reply]:
    """从 with_replies 提取结果中筛出应推送的 Alpha 楼内新回复（按 ID 升序）。

    入选条件：回复者是 handle 本人、被回复对象也是 handle（自回复）、
    父帖 ID 在活跃 Alpha 集合中、ID 大于水位（watermark=None 表示首跑不过滤）。
    """
    out: dict[str, Reply] = {}
    for r in rows:
        if (r.get("selfHandle") or "").lower() != handle.lower():
            continue
        if (r.get("parentHandle") or "").lower() != handle.lower():
            continue
        parent_id = r.get("parentId") or ""
        if parent_id not in parents:
            continue
        rid = r.get("selfId") or ""
        if not rid or rid in out:
            continue
        if watermark is not None and int(rid) <= watermark:
            continue
        out[rid] = Reply(id=rid, text=r.get("text", ""), link=r.get("selfLink", ""),
                         pub_time=r.get("pubTime", ""),
                         parent_id=parent_id, parent_link=r.get("parentLink", ""))
    return sorted(out.values(), key=lambda t: t.id_numeric)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -v`
Expected: 全量 39 passed

- [ ] **Step 5: Commit**

```bash
git add x_monitor_nodriver.py test_x_monitor.py
git commit -m "feat: select_new_replies — Alpha 楼内回复入选过滤纯函数"
```

---

### Task 4: `REPLY_EXTRACT_JS` + `BrowserSession.fetch_replies`

**Files:**
- Modify: `x_monitor_nodriver.py`（`BrowserSession` 类内，`fetch_tweets` 之后）
- Test: 本任务无法单测浏览器路径（真机验证在 Step 4），JS 结构正确性由 Task 3 的行格式契约 + 真机输出保证

**Interfaces:**
- Consumes: `_get_browser`、`_inject_cookies`、`FetchStatus`
- Produces: `async fetch_replies(cls, handle: str, config: Config) -> tuple[list[dict], FetchStatus]`——rows 即 `select_new_replies` 所需字段结构；EXPIRED 状态表示被重定向登录页

- [ ] **Step 1: 实现提取 JS 常量**

在 `TWEET_EXTRACT_JS` 之后加：

```python
    # with_replies 页提取器：X 对自回复不渲染「回复@xxx」chip（实测），
    # 父帖关系靠 article 内非时间戳的 /status/ 链接判定；排除 blockquote 引用卡。
    REPLY_EXTRACT_JS = """(() => {
        const parse = (href) => {
            const m = (href || '').match(/x\\.com\\/([^/]+)\\/status\\/(\\d+)/);
            return m ? { handle: m[1], id: m[2] } : { handle: '', id: '' };
        };
        const arts = Array.from(document.querySelectorAll('article[data-testid="tweet"]')).slice(0, 20);
        return arts.map(a => {
            const timeEl = a.querySelector('time');
            const selfA = timeEl ? timeEl.closest('a') : null;
            const selfLink = selfA ? selfA.href.split('?')[0] : '';
            const self = parse(selfLink);
            let parentLink = '';
            const anchors = Array.from(a.querySelectorAll('a[href*="/status/"]'))
                .filter(x => !x.closest('blockquote'));
            for (const x of anchors) {
                const h = x.href.split('?')[0];
                if (h && h !== selfLink) { parentLink = h; break; }
            }
            const parent = parse(parentLink);
            const textEl = a.querySelector('div[lang]');
            return {
                selfLink, selfHandle: self.handle, selfId: self.id,
                parentLink, parentHandle: parent.handle, parentId: parent.id,
                text: textEl ? textEl.textContent.trim() : '',
                pubTime: timeEl ? (timeEl.getAttribute('datetime') || '') : ''
            };
        }).filter(r => r.selfId);
    })()"""
```

- [ ] **Step 2: 实现 fetch_replies**

```python
    @classmethod
    async def fetch_replies(cls, handle: str, config: Config) -> tuple[list[dict], FetchStatus]:
        """抓取账号 with_replies 页，返回原始提取行（过滤逻辑在 select_new_replies）。"""
        browser = await cls._get_browser(config)
        url = f"https://x.com/{handle}/with_replies"
        target = None
        for tab in browser.tabs:
            if tab and tab.url and f"x.com/{handle}" in tab.url:
                target = tab
                break
        if target is None:
            target = await browser.get(url)
            await asyncio.sleep(1)
            await cls._inject_cookies(target)
            await target.get(url)
        else:
            await target.get(url)

        try:
            await target.wait_for("article", timeout=20)
        except Exception:
            log(f"[FAIL][REPLIES] @{handle}: 未等到 article (url={target.url})")
            return [], FetchStatus.FAIL

        # 触发懒加载，保证顶部窗口有足够条数
        for _ in range(2):
            await target.evaluate("window.scrollBy(0, 2000)")
            await asyncio.sleep(1.2)

        try:
            result = await target.evaluate(
                "JSON.stringify(" + cls.REPLY_EXTRACT_JS + ")",
                await_promise=True, return_by_value=True)
        except Exception as e:
            log(f"[FAIL][REPLIES] @{handle}: evaluate 失败: {e}")
            return [], FetchStatus.FAIL
        if isinstance(result, tuple):
            result = result[0]
        if hasattr(result, "value"):
            result = result.value
        try:
            rows = json.loads(result) if isinstance(result, str) else result
        except Exception as e:
            log(f"[FAIL][REPLIES] @{handle}: JSON 解析失败: {e}")
            return [], FetchStatus.FAIL

        if not rows and "login" in (target.url or "").lower():
            return [], FetchStatus.EXPIRED
        log(f"[REPLIES] @{handle}: with_replies 提取 {len(rows)} 条 article")
        return rows, FetchStatus.OK
```

- [ ] **Step 3: 全量测试回归 + Commit**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -v` → 全过

```bash
git add x_monitor_nodriver.py
git commit -m "feat: fetch_replies — with_replies 页抓取与双链接提取 JS"
```

- [ ] **Step 4: 真机冒烟（一次性，勿存文件）**

```bash
cd /home/denvor/work/xmonitor && python3 - <<'EOF'
import asyncio, json
from datetime import datetime, timezone
from x_monitor_nodriver import Config, BrowserSession, find_alpha_parents, select_new_replies
cfg = Config.load()
async def main():
    parents = find_alpha_parents("backup", datetime.now(timezone.utc))
    print(f"活跃 Alpha 父帖: {list(parents.keys())}")
    for h in cfg.handles:
        rows, st = await BrowserSession.fetch_replies(h, cfg)
        mine = [r for r in rows if r["selfHandle"].lower() == h.lower()]
        print(f"@{h}: rows={len(rows)} 本人={len(mine)} status={st.name}")
        print(f"  命中父帖的自回复: {[r['selfId'] for r in select_new_replies(rows, h, parents, None)]}")
    b = BrowserSession._browser
    if b: b.stop()
asyncio.run(main())
EOF
```

Expected: 两个账号 rows>0、status=OK；「命中父帖的自回复」应至少包含已知楼内回复（若 7 天窗口内无 Alpha 帖则 parents 为空、命中 0 条也算通过——记下实际输出供 Task 6 参考）。

---

### Task 5: 卡片渲染回复区块

**Files:**
- Modify: `x_monitor_nodriver.py`（`FeishuNotifier._build_new_tweets_card`）
- Test: `test_x_monitor.py`

**Interfaces:**
- Consumes: `AccountResult.replies`、`ALPHA_BANNER`、`Reply.beijing_time`
- Produces: 卡片 elements 中每个账号发帖条目之后跟回复条目；`has_alpha` 纳入回复（回复必属 Alpha 帖）；计数行含「N 条 Alpha 楼内回复」

- [ ] **Step 1: 写失败测试**

```python
# ── 卡片渲染：回复区块 ───────────────────────────────────────────────

class TestCardWithReplies:
    def _reply(self):
        return Reply(id="900", text="👉 领取链接在此", link="https://x.com/binancezh/status/900",
                     pub_time="2026-09-01T08:00:00.000Z",
                     parent_id="100", parent_link="https://x.com/binancezh/status/100")

    def _contents(self, card):
        return [el.get("text", {}).get("content", "") for el in card["elements"]]

    def test_replies_alone_still_marked_alpha_with_parent_link(self):
        results = [AccountResult(handle="binancezh", tweets=[], replies=[self._reply()])]
        card = FeishuNotifier._build_new_tweets_card(results, "2026-09-08 21:00")
        contents = "\n".join(self._contents(card))
        assert card["header"]["template"] == "red"
        assert "ALPHA" in card["header"]["title"]["content"]
        assert "Alpha 帖新回复" in contents
        assert "https://x.com/binancezh/status/100" in contents   # 原帖链接
        assert "Alpha 楼内回复" in contents                        # 计数行

    def test_normal_push_unchanged_when_no_replies(self):
        results = [AccountResult(handle="binancezh",
                                 tweets=[Tweet(id="1", text="普通资讯", link="l", pub_time="")])]
        card = FeishuNotifier._build_new_tweets_card(results, "2026-09-08 21:00")
        contents = "\n".join(self._contents(card))
        assert "Alpha 帖新回复" not in contents
        assert "Alpha 楼内回复" not in contents
```

（顶部 import 补 `Reply`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -k TestCardWithReplies -v`
Expected: FAIL（回复未渲染）

- [ ] **Step 3: 实现**

`_build_new_tweets_card` 内做三处修改：

```python
        # ① 计数与 has_alpha（函数开头处替换）
        total_tweets = sum(len(r.tweets) for r in results)
        total_replies = sum(len(r.replies) for r in results)
        has_alpha = total_replies > 0 or any(classify_alpha(t.text) for r in results for t in r.tweets)
```

```python
        # ② 计数行（替换原 content 赋值）
        count_line = f"推送时间：{send_time} (北京时间)\n共 {len(results)} 个账号，{total_tweets} 条新推文"
        if total_replies:
            count_line += f"，{total_replies} 条 Alpha 楼内回复"
```

```python
        # ③ 账号循环内、发帖 for 之后追加回复渲染
            for rp in r.replies:
                content = (
                    f"{ALPHA_BANNER}\n"
                    f"🧵 **Alpha 帖新回复**（回复时间：{rp.beijing_time} 北京时间）\n\n"
                    f"{rp.text}\n\n"
                    f"[↩️ 查看被回复原帖]({rp.parent_link}) ｜ [🔗 查看此回复]({rp.link})"
                )
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
                log(f"[REPLY] @{r.handle}: {rp.link} ← 父帖 {rp.parent_id}")
```

- [ ] **Step 4: 全量测试通过**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -v`
Expected: 全过（含既有 TestBuildNewTweetsCard 无回归）

- [ ] **Step 5: Commit**

```bash
git add x_monitor_nodriver.py test_x_monitor.py
git commit -m "feat: 飞书卡片渲染 Alpha 楼内回复区块（含原帖链接与计数）"
```

---

### Task 6: `Monitor.run` 接线 + 整体超时上调

**Files:**
- Modify: `x_monitor_nodriver.py`（`Monitor.run`、主入口 `wait_for(..., timeout=…)`）
- Test: `test_x_monitor.py`（monkeypatch 异步假件）

**Interfaces:**
- Consumes: Task 1 `find_alpha_parents`、Task 3 `select_new_replies`、Task 4 `BrowserSession.fetch_replies`、Task 2 `AccountResult.replies`、既有 `Cache.get/update`（键传 `f"{handle}:replies"`，Cache 本身不改）
- Produces: 完整闭环——水位首建静默、命中回复并入卡片、缓存仅在推送成功后落盘（沿用现有 `send_ok` 门）

- [ ] **Step 1: 写失败测试**

```python
# ── Monitor 回复接线（异步假件） ─────────────────────────────────────

from types import SimpleNamespace
from x_monitor_nodriver import Cache, FetchStatus, Monitor, BrowserSession, FetchResult


def _async(value):
    async def _coro(*a, **kw):
        return value
    return _coro


class TestMonitorReplyWiring:
    def _run(self, tmp_path, monkeypatch, *, cache_data, reply_rows):
        config = SimpleNamespace(handles=["binancezh"], max_retries=0, retry_delay=0, fetch_count=3)
        cache = Cache(str(tmp_path / "cache.json"))
        cache._data = dict(cache_data)
        sent = []
        notifier = FeishuNotifier("id", "secret", "chat")
        notifier._post = lambda msg_type, content: sent.append(content) or True

        monkeypatch.setattr(BrowserSession, "fetch_tweets",
                            classmethod(_async(FetchResult(tweets=[], status=FetchStatus.OK))))
        monkeypatch.setattr(BrowserSession, "fetch_replies", classmethod(_async((reply_rows, FetchStatus.OK))))
        monkeypatch.setattr("x_monitor_nodriver.find_alpha_parents",
                            lambda backup_dir, now: {"100": ("binancezh", "Alpha 主帖")})
        monkeypatch.setattr("x_monitor_nodriver._backup_tweets", lambda handle, items: None)
        asyncio.run(Monitor(config, cache, notifier).run())
        return cache, sent

    def _reply_row(self, sid="200"):
        return {"selfLink": f"https://x.com/binancezh/status/{sid}", "selfHandle": "binancezh",
                "selfId": sid, "parentLink": "https://x.com/binancezh/status/100",
                "parentHandle": "binancezh", "parentId": "100",
                "text": "👉 补充链接", "pubTime": "2026-09-01T08:00:00.000Z"}

    def test_first_run_seeds_watermark_without_pushing(self, tmp_path, monkeypatch):
        cache, sent = self._run(tmp_path, monkeypatch, cache_data={}, reply_rows=[self._reply_row()])
        assert cache.get("binancezh:replies") == "200"
        assert sent == []

    def test_new_reply_above_watermark_pushed_and_watermark_moved(self, tmp_path, monkeypatch):
        cache, sent = self._run(tmp_path, monkeypatch,
                                cache_data={"binancezh:replies": "150"}, reply_rows=[self._reply_row("200")])
        assert len(sent) == 1 and "Alpha 帖新回复" in sent[0]
        assert cache.get("binancezh:replies") == "200"

    def test_reply_below_watermark_silent(self, tmp_path, monkeypatch):
        cache, sent = self._run(tmp_path, monkeypatch,
                                cache_data={"binancezh:replies": "250"}, reply_rows=[self._reply_row("200")])
        assert sent == [] and cache.get("binancezh:replies") == "250"
```

（测试文件顶部 import 补 `import asyncio`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -k TestMonitorReplyWiring -v`
Expected: FAIL（run() 尚无回复步骤）

- [ ] **Step 3: 实现 `Monitor.run` 修改**

`run()` 中，`for handle in self.config.handles:` 之前加初始化：

```python
        alpha_parents: Optional[dict] = None  # 懒构建的活跃 Alpha 父帖集合
```

循环体内、`if account_result.tweets: ... cache.update(handle, ...)` 之后，把原来的 append 判断替换为：

```python
            # ── Alpha 楼内回复监控 ──
            if alpha_parents is None:
                alpha_parents = find_alpha_parents(
                    os.path.join(_SCRIPT_DIR, "backup"), datetime.now(timezone.utc))
                log(f"[REPLIES] 活跃 Alpha 父帖: {len(alpha_parents)} 条")

            if alpha_parents:
                try:
                    rows, rstatus = await BrowserSession.fetch_replies(handle, self.config)
                except Exception as e:
                    log(f"[FAIL][REPLIES] @{handle}: {e}")
                    rows, rstatus = [], FetchStatus.FAIL
                if rstatus == FetchStatus.EXPIRED:
                    cookie_expired = True
                    break
                if rows:
                    key = f"{handle}:replies"
                    wm = self.cache.get(key)
                    if wm is None:
                        # 首跑：只记水位（本人回复的最大 ID），不推历史
                        mine = [int(r["selfId"]) for r in rows
                                if (r.get("selfHandle") or "").lower() == handle.lower()]
                        if mine:
                            self.cache.update(key, str(max(mine)))
                            log(f"[REPLIES] @{handle}: 首次建立水位 {max(mine)}，不推送")
                    else:
                        new_replies = select_new_replies(rows, handle, alpha_parents, int(wm))
                        if len(new_replies) > MAX_REPLIES_PER_PUSH:
                            new_replies = new_replies[-MAX_REPLIES_PER_PUSH:]
                        if new_replies:
                            account_result.replies = new_replies
                            _backup_tweets(handle, new_replies)
                            self.cache.update(key, str(max(r.id_numeric for r in new_replies)))
                            log(f"[REPLIES] @{handle}: {len(new_replies)} 条 Alpha 楼内新回复")

            if account_result.tweets or account_result.replies:
                all_results.append(account_result)
```

（注意：原 `if account_result.tweets:` 块中 `all_results.append` 与 `cache.update` 的耦合要拆开——cache.update 仍在 `if account_result.tweets:` 内，append 判断移到上面替换后的位置。）

主入口超时行改为：

```python
        asyncio.run(asyncio.wait_for(monitor.run(), timeout=240))
```

对应 `except asyncio.TimeoutError:` 内的提示文案 `(120s)` → `(240s)`。

- [ ] **Step 4: 全量测试通过**

Run: `/home/denvor/.local/bin/pytest /home/denvor/work/xmonitor/test_x_monitor.py -v`
Expected: 全过

- [ ] **Step 5: 端到端真机回放验证**

临时删掉 cache.json 里的 `{handle}:replies` 键并压低水位后实跑一轮（历史 Alpha 帖 7 天窗口内如无可先手动把一个已知带楼内回复的 Alpha 推文 pubTime 改新）：

```bash
cd /home/denvor/work/xmonitor && python3 x_monitor_nodriver.py
grep -E "REPLIES|ALPHA" logs/$(date +%Y%m%d).log | tail -20
```

Expected: 日志出现 `[REPLIES] 活跃 Alpha 父帖: N 条` 与 `with_replies 提取 x 条 article`；若窗口内有符合条件的楼内回复，飞书群收到带「🔴 ALPHA｜」标题的卡片。

- [ ] **Step 6: Commit**

```bash
git add x_monitor_nodriver.py test_x_monitor.py
git commit -m "feat: Monitor 接线 Alpha 楼内回复监控（水位/推送/超时上调）"
```

---

### Task 7: 文档收尾（README + CHANGELOG）

**Files:**
- Modify: `README.md`（§1.2、§2.1 表、§3.1 流程图、§8.1、页脚版本）
- Modify: `CHANGELOG.md`（Unreleased → Added）

- [ ] **Step 1: CHANGELOG 在 `## [Unreleased]` 的 `### Added` 列表追加**

```markdown
- **Alpha 楼内回复监控**：扫描 7 天内 Alpha 类推文（数据源 `backup/`），抓取监控账号 `with_replies` 页面，识别账号在自家 Alpha 帖下的自回复（thread 楼内公告补链），合并进新帖卡片推送；去重使用独立缓存键 `{handle}:replies`
```

- [ ] **Step 2: README 更新**

1.2 核心价值追加一条：

```markdown
- **楼内追踪**：自动监控 7 天内 Alpha 推文下账号自己的追加回复（公告链接常藏在楼内），命中即以 ALPHA 卡片提醒
```

2.1 消息表「新推文（命中 Alpha 分类）」行后追加说明行，3.1 流程图「Alpha 分类」步骤后追加：

```
 ├─▶ Alpha 楼内回复监控（backup 中 7 天内 Alpha 帖 → with_replies 抓取）
 │   └─ 自回复且父帖命中 → 并入新帖卡片（🧵 Alpha 帖新回复 + 原帖链接）
```

8.1 已完成追加：

```markdown
- ✅ Alpha 楼内回复监控（with_replies 抓取、父帖链接判定、独立水位去重）
```

页脚版本 `v4.4` → `v4.5`，最后更新 `2026-09-08`，更新说明同步。

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: Alpha 楼内回复监控（README v4.5 + CHANGELOG）"
```

---

## Self-Review 结论

- **Spec 覆盖**：§1 范围表→Task 3 过滤；§2 探测结论→Task 4 JS 注释与取链接方式；§3 流程→Task 1/3/4/6；§4 呈现→Task 5；§5 缓存→Task 6（水位）；§7 错误处理→Task 4（FAIL 静默）+ Task 6（EXPIRED 中断、异常兜底）；§8 测试→各任务 Step 1 + Task 6 Step 5。
- **有意偏离**（已在 Global Constraints 声明）：spec §5 的 backup-exists 双保险改为纯水位。
- **类型一致性**：`fetch_replies` 返回 `(list[dict], FetchStatus)` 与 Task 6 调用处、`select_new_replies` 入参 rows 字段名与 Task 4 JS 输出键名逐一对齐（selfLink/selfHandle/selfId/parentLink/parentHandle/parentId/text/pubTime）。
