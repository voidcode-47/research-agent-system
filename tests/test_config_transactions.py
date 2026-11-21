"""Configuration updates preserve unrelated values and survive failed writes."""
import builtins
import io
import os
import threading
from pathlib import Path

import pytest
from dotenv import dotenv_values

from utils import env_file, prefs


@pytest.mark.parametrize("separator", ["\n", "\r"], ids=["newline", "carriage-return"])
def test_env_values_cannot_inject_another_assignment(tmp_path, separator):
    path = tmp_path / ".env"
    original = "EXISTING=keep\nMODEL=before\n"
    path.write_text(original, encoding="utf-8")
    value = f"model-value{separator}INJECTED_SETTING=unexpected"

    try:
        env_file.update_env({"MODEL": value}, path)
    except ValueError:
        assert path.read_text(encoding="utf-8") == original
    else:
        parsed = dotenv_values(stream=io.StringIO(path.read_text(encoding="utf-8")))
        assert "INJECTED_SETTING" not in parsed
        assert parsed["MODEL"] == value


def test_concurrent_env_updates_preserve_both_keys(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("EXISTING=keep\nFIRST=before\nSECOND=before\n", encoding="utf-8")
    read_bytes = Path.read_bytes
    reads = threading.Barrier(2)
    errors = []

    def read_before_either_writer(current):
        raw = read_bytes(current)
        if current == path:
            # Both unlocked readers must capture the old state. A transaction
            # lock instead serializes the readers; its first reader can proceed
            # when this barrier expires, and the second then reads the commit.
            try:
                reads.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
        return raw

    monkeypatch.setattr(Path, "read_bytes", read_before_either_writer)

    def update(key):
        try:
            env_file.update_env({key: "after"}, path)
        except Exception as error:
            errors.append(error)

    workers = [threading.Thread(target=update, args=(key,), daemon=True)
               for key in ("FIRST", "SECOND")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert all(not worker.is_alive() for worker in workers)
    assert not errors
    parsed = dotenv_values(stream=io.StringIO(path.read_text(encoding="utf-8")))
    assert parsed == {"EXISTING": "keep", "FIRST": "after", "SECOND": "after"}


@pytest.mark.parametrize("configuration", ["env", "prefs"])
def test_partial_write_failure_preserves_the_last_saved_configuration(
        tmp_path, monkeypatch, configuration):
    path = tmp_path / (".env" if configuration == "env" else "prefs.json")
    if configuration == "env":
        path.write_text("MODEL=before\nEXISTING=keep\n", encoding="utf-8")
        save = lambda: env_file.update_env({"MODEL": "after"}, path)
    else:
        monkeypatch.setattr(prefs, "PREFS_PATH", str(path))
        prefs.save_prefs("ollama", "before")
        save = lambda: prefs.save_prefs("ollama", "after")
    original = path.read_bytes()
    opened = builtins.open
    io_opened = io.open
    fd_opened = os.fdopen
    failed = []

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, value):
            self.stream.write(value[:3])
            failed.append(True)
            raise OSError("simulated partial disk write")

    def wrap_open(open_function):
        def open_with_disk_failure(file, mode="r", *args, **kwargs):
            stream = open_function(file, mode, *args, **kwargs)
            if isinstance(file, (str, Path)) and Path(file).parent == tmp_path \
                    and any(flag in mode for flag in "wax"):
                return FailingWriter(stream)
            return stream
        return open_with_disk_failure

    monkeypatch.setattr(builtins, "open", wrap_open(opened))
    monkeypatch.setattr(io, "open", wrap_open(io_opened))
    monkeypatch.setattr(os, "fdopen", lambda *a, **kw: FailingWriter(fd_opened(*a, **kw)))

    with pytest.raises(OSError, match="partial disk write"):
        save()

    assert failed, "the fault injector must exercise a real file write"
    assert path.read_bytes() == original


def test_preferences_save_failure_is_reported_to_the_caller(tmp_path, monkeypatch):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(prefs, "PREFS_PATH", str(blocked_parent / "prefs.json"))

    with pytest.raises(OSError):
        prefs.save_prefs("ollama", "new-model")

    assert blocked_parent.read_text(encoding="utf-8") == "keep"


def test_failed_env_save_does_not_change_running_settings(monkeypatch):
    import server
    monkeypatch.setattr(server.custom_providers, "migrate_legacy_custom_api", lambda: None)
    monkeypatch.setattr(server, "load_prefs", lambda: {"provider": "ollama", "model": "model"})
    monkeypatch.setattr(server.settings, "OLLAMA_BASE_URL", "http://before.invalid/v1")

    def fail(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(server, "update_env", fail)
    with pytest.raises(OSError, match="disk is full"):
        server.api_save_settings(server.SettingsRequest(provider="ollama", ollama_base_url="http://after.invalid/v1"))
    assert server.settings.OLLAMA_BASE_URL == "http://before.invalid/v1"


def test_env_roundtrips_quotes_spaces_and_comment_characters(tmp_path):
    path = tmp_path / ".env"
    value = "a token with #quotes' and \\slashes\""
    env_file.update_env({"MODEL": value}, path)
    assert dotenv_values(path)["MODEL"] == value
