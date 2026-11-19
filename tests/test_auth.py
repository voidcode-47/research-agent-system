# -*- coding: utf-8 -*-
"""管理员认证、模型权限与配额豁免测试。"""
import threading

import pytest

import server
from config.settings import settings
from fastapi.testclient import TestClient
from utils import auth, quota


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_PATH", str(tmp_path / "admin.json"))
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", str(tmp_path / "admin_credentials.txt"))
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "secret-pass-123")
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
    assert providers["cloud"]

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
    assert captured["provider"] is None
    assert captured["model"] is None


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
