"""
抓取 X (Twitter) 时间线推文 —— 通过 remote debugging port 接管已登录的 Chrome。
不启动新浏览器，直接复用已有会话。
"""

import asyncio
import json

import nodriver as uc


async def main():
    # 1. 接管 127.0.0.1:9222 上已运行的 Chrome
    browser = await uc.start(
        host="127.0.0.1",
        port=9222,
        headless=False,
    )

    # 2. 找到 x.com 的标签页；没有就打开
    target = None
    for tab in browser.tabs:
        if tab and tab.url and "x.com" in tab.url:
            target = tab
            break

    if target is None:
        target = await browser.get("https://x.com")

    # 确保导航到时间线
    await target.get("https://x.com")
    print("[info] 已导航到 https://x.com，等待页面加载...")

    # 高级等待：等待推文元素出现
    try:
        await target.wait_for("article", timeout=20)
        print("[info] 时间线已加载")
    except Exception:
        print("[warn] 未检测到 article 元素，尝试等待常见容器...")
        try:
            await target.wait_for("[data-testid='tweetText']", timeout=15)
        except Exception:
            print("[warn] 仍未检测到推文元素，尝试滚动加载...")
            await target.scroll_down()
            await asyncio.sleep(3)

    # 3. 滚动加载更多内容（确保至少有 20+ 条推文）
    print("[info] 滚动页面加载更多内容...")
    for _ in range(8):
        await target.scroll_down()
        await asyncio.sleep(1.5)

    # 4. 通过 JS 提取推文数据（X 的 href 由 JS 动态设置，CDP evaluate 可直接读取）
    js_code = """(() => {
        const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
        return articles.slice(0, 20).map(article => {
            const textEl = article.querySelector('div[lang]');
            const text = textEl ? textEl.textContent : '';
            let author = '';
            const nameLinks = article.querySelectorAll('a[role="link"]');
            for (const link of nameLinks) {
                const spans = link.querySelectorAll('span');
                for (const sp of spans) {
                    const t = sp.textContent;
                    if (t && !t.includes('@') && !t.match(/\\d+m?/) && t.length > 1 && t.length < 50) {
                        author = t;
                        break;
                    }
                }
                if (author) break;
            }
            let link = '';
            for (const a of nameLinks) {
                if (a.href && a.href.includes('/status/')) {
                    link = a.href;
                    break;
                }
            }
            return { text: text.trim(), author, link };
        });
    })()"""

    result = await target.evaluate(
        "JSON.stringify(" + js_code + ")",
        await_promise=True,
        return_by_value=True,
    )
    if isinstance(result, tuple):
        result = result[0]
    if hasattr(result, 'value'):
        result = result.value
    tweets = json.loads(result) if isinstance(result, str) else result

    if not tweets:
        print("[error] 未找到任何推文")
        print("  请确认：Chrome 已登录 X 且当前在 x.com 时间线")
        return

    print(f"[info] 共抓取到 {len(tweets)} 条推文")
    for i, t in enumerate(tweets, 1):
        print(f"  [{i:2d}] @{t['author']}: {t['text'][:60]}...")

    # 5. 写入 JSON
    output = {
        "source": "x.com timeline",
        "count": len(tweets),
        "tweets": tweets,
    }

    with open("x_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[done] 已写入 x_data.json ({len(tweets)} 条推文)")


if __name__ == "__main__":
    uc.loop().run_until_complete(main())
