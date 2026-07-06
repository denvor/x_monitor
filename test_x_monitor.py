"""Tests for x_monitor_nodriver.py configuration and browser args."""

import configparser
import os
import sys
import tempfile
from typing import Optional

# Ensure the script directory is on the path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from x_monitor_nodriver import _parse_proxy, Config


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
