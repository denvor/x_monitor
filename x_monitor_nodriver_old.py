#!/usr/bin/env python3
"""
Monitor X accounts: @binancezh, @binancewallet.
Notify via Feishu when new tweets are published.

Uses nodriver to take over an already-logged-in Chrome browser
(127.0.0.1:9222) instead of making HTTP requests.
"""

import configparser
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import nodriver as uc

# ── Config ──────────────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "config.ini")

def _load_config():
    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH, encoding="utf-8")
    return cfg

_cfg = _load_config()

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", _cfg.get("feishu", "app_id", fallback=""))
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", _cfg.get("feishu", "app_secret", fallback=""))
FEISHU_CHAT_ID = _cfg.get("feishu", "chat_id", fallback="")

CACHE_FILE = os.path.join(_SCRIPT_DIR, "cache.json")
LOG_FILE = os.path.join(_SCRIPT_DIR, "x_monitor.log")

MONITOR_HANDLES = [
    h.strip()
    for h in _cfg.get("monitor", "handles", fallback="binancezh, binancewallet").split(",")
    if h.strip()
]

FETCH_COUNT = _cfg.getint("monitor", "fetch_count", fallback=3)

MAX_RETRIES = _cfg.getint("retry", "max_retries", fallback=2)
RETRY_DELAY = _cfg.getint("retry", "retry_delay", fallback=3)

# ── Logging ─────────────────────────────────────────────────────────

_logger = logging.getLogger("x-monitor")
_logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_logger.addHandler(_fh)

_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter('%(message)s'))
_logger.addHandler(_ch)

log = _logger.info


# ── Feishu API ──────────────────────────────────────────────────────

_feishu_token_cache = {}


def _get_feishu_token():
    """Get tenant access token from Feishu API, with 1-hour cache."""
    now = time.time()
    if "token" in _feishu_token_cache and _feishu_token_cache.get("exp", 0) > now:
        return _feishu_token_cache["token"]
    try:
        from urllib.request import Request, urlopen
        payload = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
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
        _feishu_token_cache["token"] = token
        _feishu_token_cache["exp"] = now + expire - 60
        log(f"[FEISHU] Token refreshed (expires in {expire}s)")
        return token
    except Exception as e:
        log(f"[FEISHU] Failed to get token: {e}")
        return _feishu_token_cache.get("token", "")


def send_feishu_message(text: str) -> bool:
    """Send a message to Feishu via REST API. Returns True on success."""
    token = _get_feishu_token()
    if not token:
        log("[FEISHU] No token available, skipping send")
        return False
    try:
        from urllib.request import Request, urlopen
        payload = json.dumps({
            "receive_id": FEISHU_CHAT_ID,
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
            log(f"[FEISHU] Message sent successfully to {FEISHU_CHAT_ID}")
            return True
        else:
            log(f"[FEISHU] Send failed: {result}")
            return False
    except Exception as e:
        log(f"[FEISHU] Exception sending message: {e}")
        return False


# ── Cache ───────────────────────────────────────────────────────────

def load_cache():
    try:
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False)


# ── Tweet extraction JS ────────────────────────────────────────────

# Extract newest N tweets, skipping pinned tweets.
# JS runs in browser context so a.href is properly populated.
TWEET_EXTRACT_JS = """(() => {
    const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
    // Skip pinned tweets (have UserPin or tweetWithIntentHeader)
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
        // Find time element — X uses <time datetime="...">
        const timeEl = article.querySelector('time');
        if (timeEl) {
            pubTime = timeEl.getAttribute('datetime') || timeEl.getAttribute('data-time') || '';
        }
        return { text: text.trim(), link, pubTime };
    });
})()"""


# ── Browser helper ──────────────────────────────────────────────────

_browser = None


async def get_browser():
    """Lazy-init: take over existing Chrome on 127.0.0.1:9222."""
    global _browser
    if _browser is None:
        _browser = await uc.start(host="127.0.0.1", port=9222, headless=False)
    return _browser


async def fetch_tweets(handle):
    """
    Navigate to @{handle}'s profile, extract newest tweets via JS evaluate.
    Returns (tweets_list, error_code).
    error_code: None=ok, 'EXPIRED'=login redirect, 'FAIL'=other
    """
    browser = await get_browser()

    # Find existing tab or open new one
    target = None
    for tab in browser.tabs:
        if tab and tab.url and f"x.com/{handle}" in tab.url:
            target = tab
            break

    if target is None:
        target = await browser.get(f"https://x.com/{handle}")
    else:
        await target.get(f"https://x.com/{handle}")

    # Wait for tweet articles (no scrolling needed)
    try:
        await target.wait_for("article", timeout=20)
    except Exception:
        log(f"[WARN] wait_for article timed out for @{handle}, trying tweetText...")
        try:
            await target.wait_for("[data-testid='tweetText']", timeout=10)
        except Exception:
            log(f"[FAIL] Could not find tweet elements for @{handle}")
            return None, 'FAIL'

    # Extract tweets via JS
    js = TWEET_EXTRACT_JS.replace("{count}", str(FETCH_COUNT))
    try:
        result = await target.evaluate(
            "JSON.stringify(" + js + ")",
            await_promise=True,
            return_by_value=True,
        )
    except Exception as e:
        log(f"[FAIL] evaluate failed for @{handle}: {e}")
        return None, 'FAIL'

    if isinstance(result, tuple):
        result = result[0]
    if hasattr(result, 'value'):
        result = result.value

    try:
        tweets_raw = json.loads(result) if isinstance(result, str) else result
    except Exception as e:
        log(f"[FAIL] JSON parse failed for @{handle}: {e}")
        return None, 'FAIL'

    if not tweets_raw:
        # Check if page was redirected to login
        current_url = target.url.lower() if target and target.url else ""
        if 'login' in current_url:
            log(f"[EXPIRED] @{handle} redirected to login: {current_url}")
            return None, 'EXPIRED'
        log(f"[FAIL] No tweets extracted for @{handle} (page may be empty)")
        return None, 'FAIL'

    # Parse tweet data
    tweets = []
    for t in tweets_raw:
        text = t.get("text", "").strip()
        link = t.get("link", "").strip()
        pub_time = t.get("pubTime", "").strip()
        if not text or not link:
            continue
        # Extract numeric ID from link
        id_match = re.search(r'/status/(\d+)', link)
        if id_match:
            tweets.append({
                "id": id_match.group(1),
                "text": text,
                "link": link,
                "pubTime": pub_time,
            })

    if not tweets:
        log(f"[FAIL] No valid tweets for @{handle}")
        return None, 'FAIL'

    log(f"[TWEETS] @{handle}: {len(tweets)} tweets, IDs: {[t['id'] for t in tweets]}")
    for i, t in enumerate(tweets):
        log(f"  [{i+1}] ID={t['id']} | {t['text'][:80]}")

    return tweets, None


# ── Notification ────────────────────────────────────────────────────

def _format_tweet_time(pub_time_str):
    """Convert ISO datetime string to Beijing time display."""
    if not pub_time_str:
        return pub_time_str
    try:
        dt = datetime.fromisoformat(pub_time_str)
        beijing = dt + timedelta(hours=8)
        return beijing.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return pub_time_str


def _build_account_section(handle, tweets):
    """Build a text section for one account's tweets."""
    parts = []
    for i, t in enumerate(tweets, 1):
        tweet_time = _format_tweet_time(t.get("pubTime", ""))
        parts.append(
            f"{i}. 推文时间：{tweet_time} (北京时间)\n"
            f"---\n{t['text']}\n---\n"
            f"🔗 {t['link']}"
        )
        print(f"[TWEET] @{handle}: {t['link']}")
    return f"@{handle}\n\n" + "\n\n".join(parts)


def send_expired_notification():
    """Send cookie expired notification via Feishu."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    message = (
        f"⚠️ **X Cookie 已过期**\n\n"
        f"时间：{now} (北京时间)\n\n"
        f"X 监控脚本检测到 cookie 已过期（页面重定向到登录页）。\n"
        f"请在浏览器中刷新 X 页面重新登录。"
    )

    print("[EXPIRED] X cookie expired")
    print(message)

    success = send_feishu_message(message)
    if success:
        log(f"[FEISHU] ✅ Sent expired notification")
    else:
        log(f"[FEISHU] ❌ Failed to send expired notification")


# ── Main ────────────────────────────────────────────────────────────

async def main():
    log("=" * 50)
    log(f"🚀 X Monitor 启动 (nodriver)")

    start_time = time.time()
    cache = load_cache()
    cookie_expired = False
    # Collect all new tweets across accounts: list of (handle, tweets_list)
    all_sections = []
    changed = False

    for handle in MONITOR_HANDLES:
        log(f"\n{'='*50}")
        log(f"Checking @{handle}...")
        account_start = time.time()

        tweets = None
        error = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt > 0:
                log(f"   Retry {attempt}/{MAX_RETRIES}...")
                await asyncio.sleep(RETRY_DELAY)
            try:
                tweets, error = await fetch_tweets(handle)
            except Exception as e:
                log(f"   ❌ Exception: {e}")
                error = 'FAIL'
                continue

            if error == 'EXPIRED':
                log(f"❌ Cookie expired for @{handle}")
                cookie_expired = True
                break
            if tweets and len(tweets) > 0:
                break
            log(f"   Attempt {attempt+1}: got {len(tweets) if tweets else 0} tweets")

        if error == 'EXPIRED' or not tweets:
            log(f"   Skipping @{handle} (elapsed: {time.time()-account_start:.1f}s)")
            continue

        # Compare with cache
        last_id = cache.get(handle)
        max_id = max(int(t['id']) for t in tweets)

        log(f"[DEBUG] @{handle}: cached_id={last_id}, max_id={max_id}")

        new_tweets = []
        if last_id is None or last_id == '':
            log(f"📍 First run, found {len(tweets)} tweets")
            new_tweets = tweets[:FETCH_COUNT]
        elif max_id > int(last_id):
            new_tweets = [t for t in tweets if int(t['id']) > int(last_id)]
            if new_tweets:
                new_tweets = new_tweets[:FETCH_COUNT]
                log(f"   Found {len(new_tweets)} new tweets")
            else:
                new_tweets = []

        if new_tweets:
            all_sections.append((handle, new_tweets))
            cache[handle] = str(max_id)
            log(f"   Collected {len(new_tweets)} tweet(s) for @{handle}")
        else:
            log("   📋 No new tweets")

        log(f"   Elapsed: {time.time()-account_start:.1f}s")

    # ── Send one consolidated Feishu message ──
    if all_sections:
        send_time = datetime.now().strftime('%Y-%m-%d %H:%M')
        sections = []
        for handle, tweets in all_sections:
            sections.append(_build_account_section(handle, tweets))
        body = "\n\n" + "=" * 40 + "\n\n".join(sections)
        total_tweets = sum(len(tweets) for _, tweets in all_sections)
        message = (
            f"🔔 **X 新帖提醒**\n\n"
            f"推送时间：{send_time} (北京时间)\n\n"
            f"共 {len(all_sections)} 个账号，{total_tweets} 条新推文\n\n"
            f"{body}"
        )
        success = send_feishu_message(message)
        if success:
            log(f"[FEISHU] ✅ Sent consolidated notification ({len(all_sections)} account(s))")
            changed = True
        else:
            log(f"[FEISHU] ❌ Failed to send consolidated notification")
            changed = False  # Don't update cache if send failed

    if cookie_expired:
        send_expired_notification()

    if changed:
        save_cache(cache)
        log(f"\n💾 Cache: {cache}")

    total_elapsed = time.time() - start_time
    log(f"\n✅ Done in {total_elapsed:.1f}s")


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        log(f"❌ X Monitor 崩溃: {type(e).__name__}: {e}")
        log(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise
