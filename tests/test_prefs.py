"""UI 偏好持久化测试。"""
import json

import pytest

from utils import prefs


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))


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
        assert data["research"] == {"max_results": 8, "detailed": True}

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

    @pytest.mark.parametrize("payload", [None, [], "invalid", 1])
    def test_invalid_root_uses_defaults(self, tmp_path, payload):
        (tmp_path / "ui_prefs.json").write_text(json.dumps(payload), encoding="utf-8")
        assert prefs.load_prefs()["provider"] == ""

    def test_invalid_fields_do_not_replace_defaults(self, tmp_path):
        payload = {
            "provider": [], "model": 1, "custom_models": None,
            "research": {"max_results": "invalid", "detailed": None, "unknown": True},
        }
        (tmp_path / "ui_prefs.json").write_text(json.dumps(payload), encoding="utf-8")
        data = prefs.load_prefs()
        assert data["provider"] == ""
        assert data["model"] == ""
        assert data["custom_models"] == {}
        assert data["research"] == {"max_results": 8, "detailed": True}

    def test_partial_research_update_preserves_other_preferences(self):
        prefs.save_prefs("deepseek", "", research={"detailed": False})
        prefs.save_prefs("deepseek", "", research={"max_results": 6})
        assert prefs.load_prefs()["research"] == {"max_results": 6, "detailed": False}
