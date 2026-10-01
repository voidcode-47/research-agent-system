# -*- coding: utf-8 -*-
"""UI 偏好持久化测试。"""
import json

import utils.prefs as prefs


class TestPrefs:
    def test_save_load_roundtrip(self, tmp_path):
        prefs.PREFS_PATH = str(tmp_path / "ui_prefs.json")
        prefs.save_prefs("lmstudio", "qwen2.5-7b", {"lmstudio": "qwen2.5-7b"})
        data = prefs.load_prefs()
        assert data["provider"] == "lmstudio"
        assert data["model"] == "qwen2.5-7b"
        assert data["custom_models"] == {"lmstudio": "qwen2.5-7b"}

    def test_defaults_when_missing(self, tmp_path):
        prefs.PREFS_PATH = str(tmp_path / "nope.json")
        data = prefs.load_prefs()
        assert data["provider"] == ""
        assert data["model"] == ""
        assert data["custom_models"] == {}

    def test_corrupt_file_fallback(self, tmp_path):
        p = tmp_path / "ui_prefs.json"
        p.write_text("{broken json", encoding="utf-8")
        prefs.PREFS_PATH = str(p)
        data = prefs.load_prefs()
        assert data["provider"] == ""

    def test_unknown_keys_ignored(self, tmp_path):
        p = tmp_path / "ui_prefs.json"
        p.write_text(json.dumps({"provider": "ollama", "hacker": 1}), encoding="utf-8")
        prefs.PREFS_PATH = str(p)
        data = prefs.load_prefs()
        assert data["provider"] == "ollama"
        assert "hacker" not in data
