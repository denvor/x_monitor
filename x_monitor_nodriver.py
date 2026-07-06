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

    @property
    def failed(self) -> bool:
        return self.status == FetchStatus.FAIL


@dataclass
class AccountResult:
    handle: str
    tweets: list[Tweet] = field(default_factory=list)
    status: FetchStatus = FetchStatus.OK  # overall result for this account


@dataclass
class Config:
    feishu_app_id: str
    feishu_app_secret: str
    feishu_chat_id: str
    handles: list[str]
    fetch_count: int
    max_retries: int
    retry_delay: int

    @classmethod
    def load(cls) -> "Config":
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(_script_dir, "config.ini")
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


class XMonitorError(Exception):
    """Base exception for x-monitor."""


class TweetExtractionError(XMonitorError):
    pass


class FeishuSendError(XMonitorError):
    pass


# ── Logging ─────────────────────────────────────────────────────────

def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("x-monitor")
    logger.setLevel(logging.DEBUG)
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
    fh.setLevel(logging.DEBUG); fh.setFormatter(fmt_ts)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO); ch.setFormatter(fmt_flat)
    if fh is not None:
        logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

log = _setup_logger().info

_xvfb_pid: Optional[int] = None


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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _launch_chrome() -> None:
    """Prepare Xvfb virtual display for nodriver to use.

    nodriver's uc.start() will auto-detect DISPLAY and use it.
    """
    display_num = ":99"
    xvfb_cmd = f"Xvfb {display_num} -screen 0 1920x1080x24"
    log(f"[CHROME] Starting Xvfb: {xvfb_cmd}")
    xvfb = subprocess.Popen(
        xvfb_cmd.split(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    global _xvfb_pid
    _xvfb_pid = xvfb.pid

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

    @classmethod
    async def _get_browser(cls) -> "uc.Browser":
        if cls._browser is None:
            if not _check_port("127.0.0.1", 9222) or "DISPLAY" not in os.environ:
                _launch_chrome()
            cls._browser = await uc.start(
                sandbox=False,
                port=9222,
                browser_args=[
                    "--disable-dev-shm-usage",
                    "--proxy-server=http://127.0.0.1:20171",
                ],
            )
        return cls._browser

    @classmethod
    async def _inject_cookies(cls, tab: "uc.Tab") -> None:
        """Inject cookies from cookies.json into the browser via CDP."""
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        cookie_file = os.path.join(_script_dir, "cookies.json")
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
        browser = await cls._get_browser()

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


# ── Tweet Backup ──────────────────────────────────────────────────────


def _backup_tweets(handle: str, tweets: list[Tweet]) -> None:
    """Backup fetched tweets as individual JSON files in backup/ directory.

    Each tweet is stored as backup/<tweet_id>.json with full context.
    Existing files are not overwritten. Write failures are logged as warnings.
    """
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
            from urllib.request import Request, urlopen
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

    def _send(self, text: str) -> bool:
        token = self._get_token()
        if not token:
            log("[FEISHU] No token available, skipping send")
            return False
        try:
            from urllib.request import Request, urlopen
            payload = json.dumps({
                "receive_id": self.chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
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

    def send_new_tweets(self, results: list[AccountResult]) -> bool:
        """Send consolidated notification for multiple accounts."""
        send_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        sections = []
        for r in results:
            sections.append(self._build_account_section(r))
        body = "\n\n" + "=" * 40 + "\n\n".join(sections)
        total_tweets = sum(len(r.tweets) for r in results)
        message = (
            f"🔔 **X 新帖提醒**\n\n"
            f"推送时间：{send_time} (北京时间)\n\n"
            f"共 {len(results)} 个账号，{total_tweets} 条新推文\n\n"
            f"{body}"
        )
        return self._send(message)

    def send_expired(self) -> bool:
        """Send cookie expired notification."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        message = (
            f"⚠️ **X Cookie 已过期**\n\n"
            f"时间：{now} (北京时间)\n\n"
            f"X 监控脚本检测到 cookie 已过期（页面重定向到登录页）。\n"
            f"请在浏览器中刷新 X 页面重新登录。"
        )
        print("[EXPIRED] X cookie expired")
        print(message)
        return self._send(message)

    @staticmethod
    def _build_account_section(result: AccountResult) -> str:
        parts = []
        for i, t in enumerate(result.tweets, 1):
            parts.append(
                f"{i}. 推文时间：{t.beijing_time} (北京时间)\n"
                f"---\n{t.text}\n---\n"
                f"🔗 {t.link}"
            )
            print(f"[TWEET] @{result.handle}: {t.link}")
        return f"@{result.handle}\n\n" + "\n\n".join(parts)


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

        for handle in self.config.handles:
            account_result = await self._check_account(handle)

            if account_result.status == FetchStatus.EXPIRED:
                cookie_expired = True
                break

            if account_result.tweets:
                all_results.append(account_result)
                self.cache.update(handle, str(max(t.id_numeric for t in account_result.tweets)))

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
            log(f"   Attempt {attempt+1}: got {len(result.tweets) if result.tweets else 0} tweets")

        log(f"   Skipping @{handle} (elapsed: {time.time()-account_start:.1f}s)")
        return AccountResult(handle=handle)


# ── Entry Point ─────────────────────────────────────────────────────


if __name__ == "__main__":
    try:
        config = Config.load()
        _dir = os.path.dirname(os.path.abspath(__file__))
        cache = Cache(os.path.join(_dir, "cache.json"))
        cache.load()
        notifier = FeishuNotifier(config.feishu_app_id, config.feishu_app_secret, config.feishu_chat_id)
        monitor = Monitor(config, cache, notifier)
        asyncio.run(monitor.run())
    except Exception as e:
        import traceback
        log(f"❌ X Monitor 崩溃: {type(e).__name__}: {e}")
        log("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise
