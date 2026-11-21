# -*- coding: utf-8 -*-
"""管理员认证、模型权限与配额豁免测试。"""
import threading

import pytest

import server
from config.settings import settings
from fastapi.testclient import TestClient
from utils import auth, quota
from utils import custom_providers
from utils import prefs as prefs_module


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(auth, "ADMIN_PATH", str(tmp_path / "admin.json"))
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", str(tmp_path / "admin_credentials.txt"))
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "secret-pass-123")
    monkeypatch.setattr(custom_providers, "CUSTOM_PROVIDERS_PATH", str(tmp_path / "custom_providers.json"))
    monkeypatch.setattr(custom_providers, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(prefs_module, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))
    monkeypatch.setattr(settings, "CUSTOM_API_BASE_URL", "")
    auth.reset_state()
    yield
    auth.reset_state()


@pytest.fixture
def browser():
    client = TestClient(server.app)
    yield client
    client.cookies.clear()


def _login(client):
    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "secret-pass-123"})
    assert r.status_code == 200, r.text
    return r.json()


def test_login_me_logout(admin_env, browser):
    data = _login(browser)
    assert data["authenticated"] is True
    assert data["can_manage_models"] is True
    assert data["unlimited"] is True

    me = browser.get("/api/auth/me").json()
    assert me["username"] == "admin"

    providers = browser.get("/api/providers").json()
    assert providers["read_only"] is False
    assert "base_url" in providers["custom_api"]
    assert providers["cloud"] == {}

    browser.post("/api/auth/logout")
    assert browser.get("/api/auth/me").json()["authenticated"] is False


def test_login_rejects_bad_password(admin_env, browser):
    r = browser.post("/api/auth/login",
                     json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    assert "用户名或密码错误" in r.json()["detail"]


def test_ordinary_user_cannot_change_models(admin_env, browser):
    providers = browser.get("/api/providers").json()
    assert providers["read_only"] is True
    assert providers["cloud"] == {}
    assert providers["custom_api"]["api_key"] is False
    assert providers["custom_api"]["base_url"] == ""
    assert isinstance(providers["effective_model"], str)

    r = browser.post("/api/settings", json={"provider": "deepseek", "model": "x"})
    assert r.status_code == 403
    r = browser.post("/api/health-check", json={"provider": "deepseek"})
    assert r.status_code == 403


def test_ordinary_user_research_cannot_override_model(admin_env, browser, monkeypatch):
    monkeypatch.setattr(server, "load_prefs", lambda: {"provider": "ollama", "model": "chosen-model"})
    captured = {}
    done = threading.Event()

    def fake_worker(task_id, req):
        captured["provider"] = req.provider
        captured["model"] = req.model
        done.set()

    monkeypatch.setattr(server, "_run_research_worker", fake_worker)
    r = browser.post("/api/research", json={
        "topic": "测试主题", "provider": "custom", "model": "gpt-4o",
    })
    assert r.status_code == 200
    assert done.wait(3)
    assert captured["provider"] == "ollama"
    assert captured["model"] == "chosen-model"


def test_admin_research_keeps_model_override(admin_env, browser, monkeypatch):
    _login(browser)
    captured = {}
    done = threading.Event()

    def fake_worker(task_id, req):
        captured["provider"] = req.provider
        captured["model"] = req.model
        done.set()

    monkeypatch.setattr(server, "_run_research_worker", fake_worker)
    r = browser.post("/api/research", json={
        "topic": "测试主题", "provider": "deepseek", "model": "deepseek-chat",
    })
    assert r.status_code == 200
    assert done.wait(3)
    assert captured["provider"] == "deepseek"
    assert captured["model"] == "deepseek-chat"


def test_admin_quota_is_unlimited(admin_env, browser):
    _login(browser)
    snap = quota.usage_snapshot(unlimited=True)
    assert snap["unlimited"] is True
    assert snap["limit"] is None
    assert snap["remaining"] is None


def test_session_survives_server_restart(admin_env):
    token = auth.create_session("admin")
    auth.reset_state()  # 模拟重启：清空内存，再从磁盘恢复
    assert auth.session_username(token) == "admin"


def test_logout_survives_server_restart(admin_env):
    token = auth.create_session("admin")
    auth.destroy_session(token)
    auth.reset_state()
    assert auth.session_username(token) == ""


def test_admin_can_change_password_and_login_after_restart(admin_env, browser, monkeypatch):
    monkeypatch.setattr(server, "update_env", lambda *args, **kwargs: None)
    _login(browser)

    response = browser.post("/api/auth/password", json={
        "current_password": "secret-pass-123",
        "new_password": "new-secure-pass-456",
    })

    assert response.status_code == 200
    assert settings.ADMIN_PASSWORD == ""
    assert auth.login("admin", "secret-pass-123")[0] == ""
    auth.reset_state()
    assert auth.login("admin", "new-secure-pass-456")[0]


def test_password_change_requires_admin_and_correct_current_password(admin_env, browser):
    denied = browser.post("/api/auth/password", json={
        "current_password": "secret-pass-123",
        "new_password": "new-secure-pass-456",
    })
    assert denied.status_code == 403

    _login(browser)
    rejected = browser.post("/api/auth/password", json={
        "current_password": "wrong-password",
        "new_password": "new-secure-pass-456",
    })
    assert rejected.status_code == 400
    assert auth.login("admin", "secret-pass-123")[0]


def test_custom_provider_admin_crud_and_passwords_are_hidden_from_users(admin_env, browser):
    payload = {
        "name": "Main gateway",
        "protocol": "openai",
        "base_url": "https://gateway.example.test/v1",
        "api_key": "top-secret",
        "models": ["model-a", "model-b"],
        "default_model": "model-b",
    }
    assert browser.post("/api/custom-providers", json=payload).status_code == 403
    _login(browser)
    created = browser.post("/api/custom-providers", json=payload)
    assert created.status_code == 200, created.text
    service = created.json()
    assert service["models"] == payload["models"]
    assert service["has_api_key"] is True
    assert "api_key" not in service

    prefs = server.load_prefs()
    active = browser.post("/api/settings", json={
        "provider": f"custom:{service['id']}", "model": "model-a",
    })
    assert active.status_code == 200, active.text
    assert active.json()["provider"] == f"custom:{service['id']}"
    listed = browser.get("/api/providers").json()["custom_services"]
    assert len(listed) == 1 and listed[0]["id"] == service["id"]
    assert browser.delete(f"/api/custom-providers/{service['id']}").status_code == 200
    assert server.load_prefs()["provider"] == "ollama"
    assert prefs["provider"] != server.load_prefs()["provider"]


def test_removed_cloud_provider_cannot_be_selected(admin_env, browser):
    _login(browser)
    response = browser.post("/api/settings", json={"provider": "deepseek", "model": "deepseek-chat"})
    assert response.status_code == 400


def test_removing_every_model_is_rejected_without_losing_the_service(admin_env, browser):
    _login(browser)
    service = browser.post("/api/custom-providers", json={
        "name": "Gateway", "base_url": "https://gateway.example.test/v1",
        "models": ["model-a", "model-b"],
    }).json()
    response = browser.put(f"/api/custom-providers/{service['id']}", json={"models": []})

    assert response.status_code == 400
    assert browser.get("/api/providers").json()["custom_services"][0]["models"] == [
        "model-a", "model-b",
    ]


def test_migrated_service_accepts_multiple_models_and_replaces_its_key(
    admin_env, browser, monkeypatch,
):
    monkeypatch.setattr(settings, "CUSTOM_API_BASE_URL", "https://legacy.example.test/v1")
    monkeypatch.setattr(settings, "CUSTOM_API_KEY", "old-key")
    monkeypatch.setattr(settings, "CUSTOM_API_MODEL", "model-a")
    prefs_module.save_prefs("custom", "model-a")
    _login(browser)
    assert browser.get("/api/providers").json()["effective_model"] == "model-a"

    updated = browser.put("/api/custom-providers/legacy", json={
        "models": ["model-a", "model-b"], "default_model": "model-b", "api_key": "new-key",
    })
    assert updated.status_code == 200, updated.text
    assert custom_providers.get_api_key("legacy") == "new-key"
    assert browser.get("/api/providers").json()["effective_model"] == "model-a"
    active = browser.post("/api/settings", json={"provider": "custom:legacy", "model": "model-b"})
    assert active.status_code == 200, active.text
    assert browser.get("/api/providers").json()["effective_model"] == "model-b"

    assert browser.delete("/api/custom-providers/legacy").status_code == 200
    assert browser.get("/api/providers").json()["custom_services"] == []
