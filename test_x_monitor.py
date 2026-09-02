"""Tests for x_monitor_nodriver.py configuration and browser args."""

import configparser
import os
import sys
import tempfile
from typing import Optional

# Ensure the script directory is on the path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from x_monitor_nodriver import (
    _parse_proxy,
    Config,
    classify_alpha,
    AlphaCategory,
    ALPHA_BANNER,
    AccountResult,
    Tweet,
    FeishuNotifier,
)


# ── _parse_proxy ────────────────────────────────────────────────────


class TestParseProxy:
    def test_none_returns_none(self):
        """None input should return None (no proxy)."""
        assert _parse_proxy(None) is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        assert _parse_proxy("") is None

    def test_false_string_returns_none(self):
        """"false" should return None (proxy disabled)."""
        assert _parse_proxy("false") is None

    def test_false_uppercase_returns_none(self):
        """"FALSE" should return None (case insensitive)."""
        assert _parse_proxy("FALSE") is None

    def test_false_mixed_case_returns_none(self):
        """"False" should return None (case insensitive)."""
        assert _parse_proxy("False") is None

    def test_proxy_url_returns_url(self):
        """A valid proxy URL should be returned as-is."""
        url = "http://127.0.0.1:20171"
        assert _parse_proxy(url) == url

    def test_proxy_url_strips_whitespace(self):
        """Whitespace around the proxy URL should be stripped."""
        assert _parse_proxy("  http://proxy:8080  ") == "http://proxy:8080"


# ── Config [chrome] section ─────────────────────────────────────────


class TestConfigChromeSection:
    def _write_ini(self, content: str) -> str:
        """Write content to a temp ini file and return its path."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        return tmp.name

    def test_loads_proxy_and_user_data_dir(self):
        """[chrome] section should populate proxy and user_data_dir."""
        ini_path = self._write_ini(
            "[chrome]\n"
            "proxy = http://my-proxy:8888\n"
            "user_data_dir = /tmp/custom-chrome\n"
            "[feishu]\n"
            "app_id = \napp_secret = \nchat_id = \n"
            "[monitor]\n"
            "handles = test\n"
        )
        try:
            config = Config.load(ini_path)
            assert config.proxy == "http://my-proxy:8888"
            assert config.user_data_dir == "/tmp/custom-chrome"
        finally:
            os.unlink(ini_path)

    def test_proxy_false_disables_proxy(self):
        """proxy = false should result in proxy=None."""
        ini_path = self._write_ini(
            "[chrome]\n"
            "proxy = false\n"
            "user_data_dir = /tmp/xmonitor-chrome\n"
            "[feishu]\n"
            "app_id = \napp_secret = \nchat_id = \n"
            "[monitor]\n"
            "handles = test\n"
        )
        try:
            config = Config.load(ini_path)
            assert config.proxy is None
        finally:
            os.unlink(ini_path)

    def test_missing_chrome_section_uses_defaults(self):
        """No [chrome] section should use default values."""
        ini_path = self._write_ini(
            "[feishu]\n"
            "app_id = \napp_secret = \nchat_id = \n"
            "[monitor]\n"
            "handles = test\n"
        )
        try:
            config = Config.load(ini_path)
            assert config.proxy is None
            assert config.user_data_dir == "/tmp/xmonitor-chrome"
        finally:
            os.unlink(ini_path)


# ── classify_alpha ───────────────────────────────────────────────────


class TestClassifyAlpha:
    def test_non_incentive_returns_none(self):
        """A general news post should not be classified."""
        assert classify_alpha("币安发布 8 月季度报告，业绩创新高。") is None

    def test_listing_english(self):
        assert classify_alpha("Binance Alpha will be the first platform to feature Teller (DEBIT) on August 26.") is AlphaCategory.LISTING

    def test_listing_chinese(self):
        assert classify_alpha("币安 Alpha 将在 8 月 25 日成为首个上线 TermMax（TMX）的平台！") is AlphaCategory.LISTING

    def test_box(self):
        assert classify_alpha("The Binance Alpha Box is now live!") is AlphaCategory.ALPHA_BOX

    def test_box_chinese(self):
        assert classify_alpha("本次活动采用升级版的“Alpha 盲盒”模式") is AlphaCategory.ALPHA_BOX

    def test_airdrop_reminder_english(self):
        assert classify_alpha("Please get ready to claim the Binance Alpha airdrop and trade today") is AlphaCategory.AIRDROP_REMINDER

    def test_airdrop_reminder_chinese(self):
        assert classify_alpha("请大家准备今天 19:00（UTC+8）领取币安 Alpha 空投并交易！") is AlphaCategory.AIRDROP_REMINDER

    def test_airdrop_wave(self):
        assert classify_alpha("Binance Alpha's third wave of ChainOpera AI (COAI) airdrop rewards are here!") is AlphaCategory.AIRDROP_WAVE

    def test_booster(self):
        assert classify_alpha("The Phase 3 & 4 Sentio Booster Campaign rewards are now available to claim!") is AlphaCategory.BOOSTER

    def test_points_redemption(self):
        assert classify_alpha("The first Binance Alpha Points Redemption Event is now live!") is AlphaCategory.POINTS_REDEMPTION

    def test_marker_is_prominent(self):
        """ALPHA 横幅须为大字号(标题) + 着色 + 含 ALPHA 字样。"""
        assert "ALPHA" in ALPHA_BANNER
        assert "font color" in ALPHA_BANNER      # 颜色
        assert ALPHA_BANNER.startswith("#")       # 标题级大字号(且加粗)


# ── 卡片构建 ────────────────────────────────────────────────────────


class TestBuildNewTweetsCard:
    def _tweet(self, text):
        return Tweet(id="1", text=text, link="https://x.com/binancezh/status/1", pub_time="2026-08-26T07:14:57.000Z")

    def _contents(self, card):
        return [el.get("text", {}).get("content", "") for el in card["elements"]]

    def test_has_alpha_top_banner_and_red_header(self):
        results = [AccountResult(handle="binancezh", tweets=[self._tweet("币安 Alpha 将成为首个上线 X 的平台！")])]
        card = FeishuNotifier._build_new_tweets_card(results, "2026-08-26 15:00")
        assert card["header"]["template"] == "red"
        # 第一条元素是总横幅
        assert ALPHA_BANNER in self._contents(card)[0]
        # 命中推文区块顶部也含横幅
        assert any(ALPHA_BANNER in c and "上线" in c for c in self._contents(card)[1:])

    def test_no_alpha_no_banner_blue_header(self):
        results = [AccountResult(handle="binancezh", tweets=[self._tweet("币安发布季度报告，业绩创新高。")])]
        card = FeishuNotifier._build_new_tweets_card(results, "2026-08-26 15:00")
        assert card["header"]["template"] == "blue"
        assert all(ALPHA_BANNER not in c for c in self._contents(card))

    def test_mixed_only_matching_tweet_marked(self):
        results = [AccountResult(handle="binancezh", tweets=[
            self._tweet("币安 Alpha 盲盒已上线！"),
            self._tweet("普通资讯，无关激励。"),
        ])]
        card = FeishuNotifier._build_new_tweets_card(results, "2026-08-26 15:00")
        contents = self._contents(card)
        marked = [c for c in contents if "盲盒" in c or "普通资讯" in c]
        assert len([c for c in marked if ALPHA_BANNER in c]) == 1  # 只有盲盒那条
