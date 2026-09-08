"""Tests for x_monitor_nodriver.py configuration and browser args."""

import configparser
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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
        # 标题含 ALPHA，通知预览可直接分辨
        assert "ALPHA" in card["header"]["title"]["content"]
        # 第一条元素是总横幅
        assert ALPHA_BANNER in self._contents(card)[0]
        # 命中推文区块顶部也含横幅
        assert any(ALPHA_BANNER in c and "上线" in c for c in self._contents(card)[1:])

    def test_no_alpha_no_banner_blue_header(self):
        results = [AccountResult(handle="binancezh", tweets=[self._tweet("币安发布季度报告，业绩创新高。")])]
        card = FeishuNotifier._build_new_tweets_card(results, "2026-08-26 15:00")
        assert card["header"]["template"] == "blue"
        assert "ALPHA" not in card["header"]["title"]["content"]
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
        self._mk(tmp_path, "200", "binancezh", self.ALPHA_TEXT,
                 now - timedelta(days=ALPHA_REPLY_WINDOW_DAYS, minutes=1))
        self._mk(tmp_path, "201", "binancezh", self.ALPHA_TEXT,
                 now - timedelta(days=ALPHA_REPLY_WINDOW_DAYS - 0.01))
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
