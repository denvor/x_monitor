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
    fmt_ts = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt_flat = logging.Formatter("%(message)s")
    fh = logging.FileHandler(os.path.join(_dir, "x_monitor.log"), encoding="utf-8")
    fh.setLevel(logging.DEBUG); fh.setFormatter(fmt_ts)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO); ch.setFormatter(fmt_flat)
    logger.addHandler(fh); logger.addHandler(ch)
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


class BrowserSession:
    """Manage nodriver browser connection and tweet extraction."""

    _browser: Optional["uc.Browser"] = None

    # Extract newest N tweets, skipping pinned tweets.
    TWEET_EXTRACT_JS = """(() => {
        const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
        const normal = articles.filter(a =>
            !a.querySelector('[data-testid="UserPin"]') &&
            !a.querySelector('[data-testid="tweetWithIntentHeader"]')
        );
        return normal.slice(0, {count}).map(article => {
            const textEl = article.querySelector('div[lang]');
            const text = textEl ? textEl.textContent : '';
            let link = '';
            let pubTime = '';
            const allLinks = article.querySelectorAll('a[href]');
            for (const a of allLinks) {
                if (a.href && a.href.includes('/status/')) {
                    link = a.href;
                    break;
                }
            }
            const timeEl = article.querySelector('time');
            if (timeEl) {
                pubTime = timeEl.getAttribute('datetime') || timeEl.getAttribute('data-time') || '';
            }
            return { text: text.trim(), link, pubTime };
        });
    })()"""

    @classmethod
    async def _get_browser(cls) -> "uc.Browser":
        if cls._browser is None:
            cls._browser = await uc.start(host="127.0.0.1", port=9222, headless=False)
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
            log(f"[COOKIE] Injected {len(cookies)} cookies for {tab.url}")
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
            # No existing tab means no cookies yet — inject them
            await cls._inject_cookies(target)
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

        return FetchResult(tweets=tweets, status=FetchStatus.OK)


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
