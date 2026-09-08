#!/usr/bin/env python3
"""
Monitor X accounts: @binancezh, @binancewallet.
Notify via Feishu when new tweets are published.

Uses nodriver to take over an already-logged-in Chrome browser
(127.0.0.1:9222) instead of making HTTP requests.
"""

import asyncio
import configparser
import json
import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import nodriver as uc
from nodriver.cdp import network
from nodriver.cdp.network import CookieParam
from urllib.request import Request, urlopen

# ── Module Constants ────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Data Models ─────────────────────────────────────────────────────


class FetchStatus(Enum):
    OK = "ok"
    EXPIRED = "expired"
    FAIL = "fail"


@dataclass
class Tweet:
    id: str
    text: str
    link: str
    pub_time: str

    @property
    def id_numeric(self) -> int:
        return int(self.id)

    @property
    def beijing_time(self) -> str:
        if not self.pub_time:
            return self.pub_time
        try:
            dt = datetime.fromisoformat(self.pub_time)
            beijing = dt + timedelta(hours=8)
            return beijing.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return self.pub_time


@dataclass
class FetchResult:
    tweets: list[Tweet] = field(default_factory=list)
    status: FetchStatus = FetchStatus.OK

    @property
    def expired(self) -> bool:
        return self.status == FetchStatus.EXPIRED


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


# ── Binance Alpha Classification ──────────────────────────────────────
# 分类「币安 Alpha」生态的可领取激励推文，用于推送时打上显著的 ALPHA 标记。
# 飞书卡片 lark_md：`# ` 为标题级大字号且加粗，<font color='red'> 着色。

ALPHA_BANNER = "# <font color='red'>🔴 ALPHA</font>"


class AlphaCategory(Enum):
    """币安 Alpha 激励推文类别。"""

    LISTING = "新币上线"
    ALPHA_BOX = "Alpha Box 盲盒"
    AIRDROP_REMINDER = "空投领取提醒"
    AIRDROP_WAVE = "空投奖励发放"
    BOOSTER = "Booster 活动"
    POINTS_REDEMPTION = "积分兑换"


def classify_alpha(text: str) -> Optional[AlphaCategory]:
    """将推文正文分类为币安 Alpha 激励类型。

    命中任一类别返回对应的 AlphaCategory；不匹配返回 None。
    按优先级匹配（Booster > 盲盒 > 积分兑换 > 空投 Wave > 空投提醒 > 新币上线）。
    """
    txt = text.lower()
    if "booster" in txt:
        return AlphaCategory.BOOSTER
    if "alpha box" in txt or "盲盒" in txt:
        return AlphaCategory.ALPHA_BOX
    if "redemption" in txt or "redeem" in txt:
        return AlphaCategory.POINTS_REDEMPTION
    if "airdrop rewards are here" in txt or "wave of" in txt:
        return AlphaCategory.AIRDROP_WAVE
    if "claim the binance alpha airdrop" in txt or "领取币安 alpha 空投" in txt:
        return AlphaCategory.AIRDROP_REMINDER
    if "first platform to feature" in txt or "成为首个上线" in txt:
        return AlphaCategory.LISTING
    return None


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


def _parse_proxy(value: Optional[str]) -> Optional[str]:
    """Parse proxy config value.

    Returns None if proxy is disabled (false/empty),
    otherwise returns the proxy URL string.
    """
    if not value or value.strip().lower() == "false":
        return None
    return value.strip()


@dataclass
class Config:
    feishu_app_id: str
    feishu_app_secret: str
    feishu_chat_id: str
    handles: list[str]
    fetch_count: int
    max_retries: int
    retry_delay: int
    proxy: Optional[str] = None
    user_data_dir: str = "/tmp/xmonitor-chrome"

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        if config_path is None:
            config_path = os.path.join(_SCRIPT_DIR, "config.ini")
        cfg = configparser.ConfigParser()
        cfg.read(config_path, encoding="utf-8")

        return cls(
            feishu_app_id=os.environ.get("FEISHU_APP_ID", cfg.get("feishu", "app_id", fallback="")),
            feishu_app_secret=os.environ.get("FEISHU_APP_SECRET", cfg.get("feishu", "app_secret", fallback="")),
            feishu_chat_id=cfg.get("feishu", "chat_id", fallback=""),
            handles=[h.strip() for h in cfg.get("monitor", "handles", fallback="binancezh, binancewallet").split(",") if h.strip()],
            fetch_count=cfg.getint("monitor", "fetch_count", fallback=3),
            max_retries=cfg.getint("retry", "max_retries", fallback=2),
            retry_delay=cfg.getint("retry", "retry_delay", fallback=3),
            proxy=_parse_proxy(cfg.get("chrome", "proxy", fallback=None)),
            user_data_dir=cfg.get("chrome", "user_data_dir", fallback="/tmp/xmonitor-chrome"),
        )


class Cache:
    def __init__(self, path: str):
        self._path = path
        self._data: dict[str, str] = {}

    def load(self) -> None:
        try:
            with open(self._path, "r") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def save(self) -> None:
        with open(self._path, "w") as f:
            json.dump(self._data, f, ensure_ascii=False)

    def get(self, handle: str) -> Optional[str]:
        return self._data.get(handle)

    def update(self, handle: str, tweet_id: str) -> None:
        self._data[handle] = tweet_id


# ── Exceptions ──────────────────────────────────────────────────────


# ── Logging ─────────────────────────────────────────────────────────

def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("x-monitor")
    logger.setLevel(logging.DEBUG)
    _log_dir = os.path.join(_SCRIPT_DIR, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _today = datetime.now().strftime("%Y%m%d")
    _log_file = os.path.join(_log_dir, f"{_today}.log")
    fmt_ts = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt_flat = logging.Formatter("%(message)s")
    try:
        fh = logging.FileHandler(_log_file, encoding="utf-8")
    except OSError:
        logger.warning("[WARN] Could not create log file %s, falling back to stderr", _log_file)
        fh = None
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt_flat)
    if fh is not None:
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt_ts)
        logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

log = _setup_logger().info


# ── Browser Session ─────────────────────────────────────────────────


def _load_cookies(path: str) -> list[CookieParam]:
    """Load cookies from a Firefox-format cookies.json and convert to CDP CookieParam."""
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    cookies: list[CookieParam] = []
    for c in raw:
        name = c.get("name", "").strip()
        value = c.get("value", "").strip()
        if not name or not value:
            continue
        cookies.append(CookieParam(name=name, value=value, domain=c.get("domain", "")))
    return cookies


def _check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _launch_chrome() -> None:
    """Prepare Xvfb virtual display for nodriver to use.

    nodriver's uc.start() will auto-detect DISPLAY and use it.
    """
    display_num = ":99"
    xvfb_cmd = f"Xvfb {display_num} -screen 0 1920x1080x24"
    log(f"[CHROME] Starting Xvfb: {xvfb_cmd}")
    subprocess.Popen(
        xvfb_cmd.split(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for Xvfb to be ready
    for _ in range(20):
        env = {**os.environ, "DISPLAY": display_num}
        result = subprocess.run(
            ["xdpyinfo", "-display", display_num],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            break
        time.sleep(0.5)

    os.environ["DISPLAY"] = display_num
    log(f"[CHROME] DISPLAY={display_num} set for nodriver")


class BrowserSession:
    """Manage nodriver browser connection and tweet extraction."""

    _browser: Optional["uc.Browser"] = None

    # Extract newest N tweets, skipping pinned tweets and deduplicating by link.
    TWEET_EXTRACT_JS = """(() => {
        const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
        const normal = articles.filter(a =>
            !a.querySelector('[data-testid="UserPin"]') &&
            !a.querySelector('[data-testid="tweetWithIntentHeader"]')
        );
        const seen = new Set();
        return normal.slice(0, {count}).reduce((acc, article) => {
            let link = '';
            const allLinks = article.querySelectorAll('a[href]');
            for (const a of allLinks) {
                if (a.href && a.href.includes('/status/')) {
                    link = a.href;
                    break;
                }
            }
            if (!link || seen.has(link)) return acc;
            seen.add(link);
            const textEl = article.querySelector('div[lang]');
            const text = textEl ? textEl.textContent : '';
            let pubTime = '';
            const timeEl = article.querySelector('time');
            if (timeEl) {
                pubTime = timeEl.getAttribute('datetime') || timeEl.getAttribute('data-time') || '';
            }
            acc.push({ text: text.trim(), link, pubTime });
            return acc;
        }, []);
    })()"""

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

    @classmethod
    async def _get_browser(cls, config: Config) -> "uc.Browser":
        if cls._browser is None:
            if not _check_port("127.0.0.1", 9222) or "DISPLAY" not in os.environ:
                _launch_chrome()
            browser_args = ["--disable-dev-shm-usage", "--no-sandbox"]
            if config.proxy:
                browser_args.append(f"--proxy-server={config.proxy}")
            browser_args.append(f"--user-data-dir={config.user_data_dir}")
            cls._browser = await uc.start(
                sandbox=False,
                port=9222,
                browser_args=browser_args,
            )
        return cls._browser

    @classmethod
    async def _inject_cookies(cls, tab: "uc.Tab") -> None:
        """Inject cookies from cookies.json into the browser via CDP."""
        cookie_file = os.path.join(_SCRIPT_DIR, "cookies.json")
        cookies = _load_cookies(cookie_file)
        if not cookies:
            log("[COOKIE] cookies.json not found or empty, skipping injection")
            return
        try:
            gen = network.set_cookies(cookies=cookies)
            await tab.send(gen)
            current_url = tab.url or "(no url yet)"
            log(f"[COOKIE] Injected {len(cookies)} cookies (current url: {current_url})")
        except Exception as e:
            log(f"[COOKIE] Failed to inject cookies: {e}")

    @classmethod
    async def fetch_tweets(cls, handle: str, config: Config) -> FetchResult:
        browser = await cls._get_browser(config)

        # Find existing tab or open new one
        target = None
        for tab in browser.tabs:
            if tab and tab.url and f"x.com/{handle}" in tab.url:
                target = tab
                break

        if target is None:
            target = await browser.get(f"https://x.com/{handle}")
            # Wait briefly for page context, then inject cookies
            await asyncio.sleep(1)
            await cls._inject_cookies(target)
            # Re-navigate so cookies take effect
            await target.get(f"https://x.com/{handle}")
        else:
            await target.get(f"https://x.com/{handle}")

        # Wait for tweet articles
        try:
            await target.wait_for("article", timeout=20)
        except Exception:
            log(f"[WARN] wait_for article timed out for @{handle}, trying tweetText...")
            try:
                await target.wait_for("[data-testid='tweetText']", timeout=10)
            except Exception:
                log(f"[FAIL] Could not find tweet elements for @{handle}")
                return FetchResult(status=FetchStatus.FAIL)

        # Extract tweets via JS
        js = cls.TWEET_EXTRACT_JS.replace("{count}", str(config.fetch_count))
        try:
            result = await target.evaluate(
                "JSON.stringify(" + js + ")",
                await_promise=True,
                return_by_value=True,
            )
        except Exception as e:
            log(f"[FAIL] evaluate failed for @{handle}: {e}")
            return FetchResult(status=FetchStatus.FAIL)

        if isinstance(result, tuple):
            result = result[0]
        if hasattr(result, "value"):
            result = result.value

        try:
            tweets_raw = json.loads(result) if isinstance(result, str) else result
        except Exception as e:
            log(f"[FAIL] JSON parse failed for @{handle}: {e}")
            return FetchResult(status=FetchStatus.FAIL)

        if not tweets_raw:
            current_url = target.url.lower() if target and target.url else ""
            if "login" in current_url:
                log(f"[EXPIRED] @{handle} redirected to login: {current_url}")
                return FetchResult(status=FetchStatus.EXPIRED)
            log(f"[FAIL] No tweets extracted for @{handle} (page may be empty)")
            return FetchResult(status=FetchStatus.FAIL)

        # Parse tweet data
        tweets = []
        for t in tweets_raw:
            text = t.get("text", "").strip()
            link = t.get("link", "").strip()
            pub_time = t.get("pubTime", "").strip()
            if not text or not link:
                continue
            id_match = re.search(r"/status/(\d+)", link)
            if id_match:
                tweets.append(Tweet(id=id_match.group(1), text=text, link=link, pub_time=pub_time))

        if not tweets:
            log(f"[FAIL] No valid tweets for @{handle}")
            return FetchResult(status=FetchStatus.FAIL)

        log(f"[TWEETS] @{handle}: {len(tweets)} tweets, IDs: {[t.id for t in tweets]}")
        for i, t in enumerate(tweets):
            log(f"  [{i+1}] ID={t.id} | {t.text[:80]}")

        _backup_tweets(handle, tweets)

        return FetchResult(tweets=tweets, status=FetchStatus.OK)

    @classmethod
    async def fetch_replies(cls, handle: str, config: Config) -> "tuple[list[dict], FetchStatus]":
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


# ── Tweet Backup ──────────────────────────────────────────────────────


def _backup_tweets(handle: str, tweets: list[Tweet]) -> None:
    """Backup fetched tweets as individual JSON files in backup/ directory.

    Each tweet is stored as backup/<tweet_id>.json with full context.
    Existing files are not overwritten. Write failures are logged as warnings.
    """
    _backup_dir = os.path.join(_SCRIPT_DIR, "backup")
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
        if getattr(tweet, "parent_link", ""):
            _data["parent_link"] = tweet.parent_link
        try:
            with open(_path, "w", encoding="utf-8") as f:
                json.dump(_data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log(f"[BACKUP] Failed to write {tweet.id}: {e}")


# ── Feishu Notifier ─────────────────────────────────────────────────


class FeishuNotifier:
    """Send notifications to Feishu via REST API."""

    _token_cache: dict = {}

    def __init__(self, app_id: str, app_secret: str, chat_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id

    def _get_token(self) -> str:
        now = time.time()
        if "token" in self._token_cache and self._token_cache.get("exp", 0) > now:
            return self._token_cache["token"]
        try:
            payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
            req = Request(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            resp = urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            token = data.get("tenant_access_token", "")
            expire = data.get("expire", 7200)
            self._token_cache["token"] = token
            self._token_cache["exp"] = now + expire - 60
            log(f"[FEISHU] Token refreshed (expires in {expire}s)")
            return token
        except Exception as e:
            log(f"[FEISHU] Failed to get token: {e}")
            return self._token_cache.get("token", "")

    def _post(self, msg_type: str, content: str) -> bool:
        """Post a message of the given type to the chat. `content` is a JSON string."""
        token = self._get_token()
        if not token:
            log("[FEISHU] No token available, skipping send")
            return False
        try:
            payload = json.dumps({
                "receive_id": self.chat_id,
                "msg_type": msg_type,
                "content": content,
            }).encode()
            req = Request(
                f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
            )
            resp = urlopen(req, timeout=10)
            result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                log(f"[FEISHU] Message sent successfully to {self.chat_id}")
                return True
            else:
                log(f"[FEISHU] Send failed: {result}")
                return False
        except Exception as e:
            log(f"[FEISHU] Exception sending message: {e}")
            return False

    def _send_card(self, card: dict) -> bool:
        """Send an interactive card message."""
        return self._post("interactive", json.dumps(card))

    def send_new_tweets(self, results: list[AccountResult]) -> bool:
        """Send consolidated card notification for multiple accounts."""
        send_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        card = self._build_new_tweets_card(results, send_time)
        return self._send_card(card)

    @staticmethod
    def _build_new_tweets_card(results: list[AccountResult], send_time: str) -> dict:
        """Build a Feishu interactive card for new tweets.

        命中 Alpha 分类的推文，在其区块顶部加一条红色大字号横幅；
        若本批存在命中推文，整条消息顶部再加一条总横幅（header 同步变红）。
        """
        total_tweets = sum(len(r.tweets) for r in results)
        total_replies = sum(len(r.replies) for r in results)
        # 回复的父帖必为 Alpha → 有回复即视为 Alpha 批次
        has_alpha = total_replies > 0 or any(classify_alpha(t.text) for r in results for t in r.tweets)

        elements = []
        if has_alpha:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": ALPHA_BANNER}})
        count_line = f"推送时间：{send_time} (北京时间)\n共 {len(results)} 个账号，{total_tweets} 条新推文"
        if total_replies:
            count_line += f"，{total_replies} 条 Alpha 楼内回复"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": count_line},
        })

        for r in results:
            elements.append({"tag": "hr"})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**@{r.handle}**"}})
            for i, t in enumerate(r.tweets, 1):
                banner = f"{ALPHA_BANNER}\n" if classify_alpha(t.text) else ""
                content = (
                    f"{banner}{i}. 推文时间：{t.beijing_time} (北京时间)\n\n"
                    f"{t.text}\n\n"
                    f"[🔗 查看原帖]({t.link})"
                )
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
                log(f"[TWEET] @{r.handle}: {t.link}")
            # Alpha 帖楼内新回复区块（父帖必为 Alpha → 一律带横幅）
            for rp in r.replies:
                content = (
                    f"{ALPHA_BANNER}\n"
                    f"🧵 **Alpha 帖新回复**（回复时间：{rp.beijing_time} 北京时间）\n\n"
                    f"{rp.text}\n\n"
                    f"[↩️ 查看被回复原帖]({rp.parent_link}) ｜ [🔗 查看此回复]({rp.link})"
                )
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
                log(f"[REPLY] @{r.handle}: {rp.link} ← 父帖 {rp.parent_id}")

        # 命中 Alpha 时标题带 ALPHA 字样，便于在飞书消息通知预览中直接分辨
        title = "🔴 ALPHA｜X 新帖提醒" if has_alpha else "🔔 X 新帖提醒"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "lark_md", "content": title},
                "template": "red" if has_alpha else "blue",
            },
            "elements": elements,
        }

    def send_expired(self) -> bool:
        """Send cookie expired notification as a card."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        log("[EXPIRED] X cookie expired")
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "lark_md", "content": "⚠️ X Cookie 已过期"},
                "template": "orange",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": f"时间：{now} (北京时间)\n\nX 监控脚本检测到 cookie 已过期（页面重定向到登录页）。\n请在浏览器中刷新 X 页面重新登录。"}},
            ],
        }
        return self._send_card(card)


# ── Monitor Orchestrator ────────────────────────────────────────────


class Monitor:
    """Orchestrate the monitoring loop: fetch, deduplicate, notify, cache."""

    def __init__(self, config: Config, cache: Cache, notifier: FeishuNotifier):
        self.config = config
        self.cache = cache
        self.notifier = notifier

    async def run(self) -> None:
        log("=" * 50)
        log(f"🚀 X Monitor 启动 (nodriver)")

        start_time = time.time()
        cookie_expired = False
        all_results: list[AccountResult] = []

        alpha_parents: Optional[dict] = None  # 懒构建的活跃 Alpha 父帖集合

        for handle in self.config.handles:
            account_result = await self._check_account(handle)

            if account_result.status == FetchStatus.EXPIRED:
                cookie_expired = True
                break

            if account_result.tweets:
                self.cache.update(handle, str(max(t.id_numeric for t in account_result.tweets)))

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
                    # 无水位（首跑）不做静默种子：父帖 7 天窗口本身已压制历史噪音，命中即推
                    new_replies = select_new_replies(rows, handle, alpha_parents,
                                                     int(wm) if wm else None)
                    if len(new_replies) > MAX_REPLIES_PER_PUSH:
                        new_replies = new_replies[-MAX_REPLIES_PER_PUSH:]
                    if new_replies:
                        account_result.replies = new_replies
                        _backup_tweets(handle, new_replies)
                        self.cache.update(key, str(max(r.id_numeric for r in new_replies)))
                        log(f"[REPLIES] @{handle}: {len(new_replies)} 条 Alpha 楼内新回复")

            if account_result.tweets or account_result.replies:
                all_results.append(account_result)

        # Send consolidated notification
        send_ok = False
        if all_results:
            send_ok = self.notifier.send_new_tweets(all_results)

        if cookie_expired:
            self.notifier.send_expired()

        if send_ok:
            self.cache.save()
            log(f"\n💾 Cache: {self.cache._data}")

        total_elapsed = time.time() - start_time
        log(f"\n✅ Done in {total_elapsed:.1f}s")

    async def _check_account(self, handle: str) -> AccountResult:
        log(f"\n{'='*50}")
        log(f"Checking @{handle}...")
        account_start = time.time()

        for attempt in range(self.config.max_retries + 1):
            if attempt > 0:
                log(f"   Retry {attempt}/{self.config.max_retries}...")
                await asyncio.sleep(self.config.retry_delay)
            try:
                result = await BrowserSession.fetch_tweets(handle, self.config)
            except Exception as e:
                log(f"   ❌ Exception: {e}")
                continue

            if result.expired:
                log(f"❌ Cookie expired for @{handle}")
                return AccountResult(handle=handle, status=FetchStatus.EXPIRED)

            # Deduplicate: keep only tweets newer than cached max ID
            cached_id = self.cache.get(handle)
            if cached_id:
                cached_num = int(cached_id)
                new_tweets = [t for t in result.tweets if t.id_numeric > cached_num]
                log(f"   [DEDUP] @{handle}: cached_id={cached_num}, fetched={len(result.tweets)}, new={len(new_tweets)}")
                result.tweets = new_tweets

            if result.tweets:
                return AccountResult(handle=handle, tweets=result.tweets)
            log(f"   Attempt {attempt+1}: got {len(result.tweets)} tweets")

        log(f"   Skipping @{handle} (elapsed: {time.time()-account_start:.1f}s)")
        return AccountResult(handle=handle)


# ── Entry Point ─────────────────────────────────────────────────────


if __name__ == "__main__":
    try:
        config = Config.load()
        cache = Cache(os.path.join(_SCRIPT_DIR, "cache.json"))
        cache.load()
        notifier = FeishuNotifier(config.feishu_app_id, config.feishu_app_secret, config.feishu_chat_id)
        monitor = Monitor(config, cache, notifier)
        asyncio.run(asyncio.wait_for(monitor.run(), timeout=240))
    except asyncio.TimeoutError:
        log("❌ X Monitor 超时（240s），强制退出")
        sys.exit(1)
    except Exception as e:
        import traceback
        log(f"❌ X Monitor 崩溃: {type(e).__name__}: {e}")
        log("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise
