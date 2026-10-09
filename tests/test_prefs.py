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


class TestSceneModelPool:
    """分场景模型池：旧配置迁移播种、保存往返、显式清空语义。"""

    def test_migrate_seeds_pool_from_legacy(self, tmp_path):
        """旧配置（无 scene_models）加载后应给各场景播种当前激活模型，
        保证升级后侧边栏「当前功能模型列表」立刻有内容可选。"""
        p = tmp_path / "ui_prefs.json"
        p.write_text(json.dumps({"provider": "deepseek", "model": "deepseek-chat"}),
                     encoding="utf-8")
        prefs.PREFS_PATH = str(p)
        data = prefs.load_prefs()
        chat = data["scene_models"]["chat"]
        assert chat["models"] == [{"provider": "deepseek", "model": "deepseek-chat"}]
        kb = data["scene_models"]["kb"]
        assert kb["models"] == [{"provider": "deepseek", "model": "deepseek-chat"}]

    def test_migrate_keeps_existing_pool(self, tmp_path):
        """已有多模型池的配置加载时不得被迁移逻辑覆盖。"""
        p = tmp_path / "ui_prefs.json"
        p.write_text(json.dumps({
            "provider": "deepseek", "model": "deepseek-chat",
            "scene_models": {"chat": {
                "provider": "zhipu", "model": "glm-4-flash",
                "models": [{"provider": "zhipu", "model": "glm-4-flash"},
                           {"provider": "deepseek", "model": "deepseek-chat"}],
            }},
        }), encoding="utf-8")
        prefs.PREFS_PATH = str(p)
        chat = prefs.load_prefs()["scene_models"]["chat"]
        assert chat["provider"] == "zhipu"
        assert chat["models"] == [{"provider": "zhipu", "model": "glm-4-flash"},
                                  {"provider": "deepseek", "model": "deepseek-chat"}]

    def test_save_pool_roundtrip(self, tmp_path):
        prefs.PREFS_PATH = str(tmp_path / "ui_prefs.json")
        pool = [{"provider": "deepseek", "model": "deepseek-chat"},
                {"provider": "ollama", "model": "qwen2.5:7b"}]
        prefs.save_prefs("deepseek", "deepseek-chat",
                         scene_models={"chat": {"provider": "zhipu",
                                                "model": "glm-4-flash", "models": pool}})
        chat = prefs.load_prefs()["scene_models"]["chat"]
        assert chat["models"] == pool
        assert chat["provider"] == "zhipu"
        assert chat["model"] == "glm-4-flash"

    def test_save_empty_string_clears_but_none_keeps(self, tmp_path):
        """provider/model：空串 = 显式清空；缺省（None）= 保持原值。"""
        prefs.PREFS_PATH = str(tmp_path / "ui_prefs.json")
        prefs.save_prefs("deepseek", "deepseek-chat",
                         scene_models={"chat": {"provider": "deepseek",
                                                "model": "deepseek-chat"}})
        # 只带 models、不带 provider/model → 原激活值保持
        prefs.save_prefs("deepseek", "deepseek-chat",
                         scene_models={"chat": {"models": [
                             {"provider": "deepseek", "model": "deepseek-chat"}]}})
        chat = prefs.load_prefs()["scene_models"]["chat"]
        assert chat["provider"] == "deepseek"
        assert len(chat["models"]) == 1
        # 空串 → 显式清空激活项
        prefs.save_prefs("deepseek", "deepseek-chat",
                         scene_models={"chat": {"provider": "", "model": ""}})
        chat = prefs.load_prefs()["scene_models"]["chat"]
        assert chat["provider"] == ""
        assert chat["model"] == ""

    def test_norm_pool_dedup_and_drop_invalid(self):
        pool = prefs._norm_pool([
            {"provider": "deepseek", "model": "m1"},
            {"provider": "deepseek", "model": "m1"},      # 重复 → 去重
            {"provider": "", "model": "m2"},              # 空 provider → 丢弃
            "not-a-dict",                                 # 非法结构 → 丢弃
            {"provider": "zhipu", "model": "m3", "extra": 1},  # 多余字段剥掉
        ])
        assert pool == [{"provider": "deepseek", "model": "m1"},
                        {"provider": "zhipu", "model": "m3"}]
