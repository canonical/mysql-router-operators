# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import signal
from unittest.mock import MagicMock

import pytest
from common.watchdog import service as service_module
from common.watchdog.service import MAIN_PATH, WatchdogService

CHARM_DIR_NAME = "charm"


@pytest.fixture(autouse=True)
def no_watchdog_process():
    """Override the conftest fixture: these tests exercise the real `WatchdogService`.

    `subprocess.Popen` and `os.kill` are patched by the `popen` and `kill` fixtures instead
    """


@pytest.fixture
def popen(monkeypatch):
    mock = MagicMock()
    mock.return_value.pid = 4242
    monkeypatch.setattr(service_module.subprocess, "Popen", mock)
    return mock


@pytest.fixture
def kill(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(service_module.os, "kill", mock)
    return mock


def _cmdline(monkeypatch, value):
    monkeypatch.setattr(service_module, "_proc_cmdline", lambda pid: value)


def _ensure(svc, tmp_path, password="pw"):
    svc.ensure_running(
        unit_name="mysql-router-k8s/0",
        charm_dir=tmp_path / CHARM_DIR_NAME,
        username="monitoring",
        password=password,
    )


def test_ensure_running_spawns_and_writes_files(tmp_path, popen, monkeypatch):
    monkeypatch.setenv("JUJU_CONTEXT_ID", "ctx")
    svc = WatchdogService(agent_dir=tmp_path)
    _ensure(svc, tmp_path)

    args, kwargs = popen.call_args
    assert args[0] == [
        "/usr/bin/python3",
        str(MAIN_PATH),
        "mysql-router-k8s/0",
        str(tmp_path / CHARM_DIR_NAME),
        str(svc.cred_file),
    ]
    assert "JUJU_CONTEXT_ID" not in kwargs["env"]
    assert kwargs["start_new_session"] is True
    assert svc.cred_file.read_text() == "monitoring:pw"
    assert svc.cred_file.stat().st_mode & 0o777 == 0o600
    assert svc.pid_file.read_text().strip() == "4242"


def test_ensure_running_noop_when_running(tmp_path, popen, monkeypatch):
    svc = WatchdogService(agent_dir=tmp_path)
    svc.pid_file.write_text("99")
    _cmdline(monkeypatch, "/usr/bin/python3\0/x/watchdog/main.py\0a")
    _ensure(svc, tmp_path)
    popen.assert_not_called()


def test_stale_pid_respawns(tmp_path, popen, monkeypatch):
    svc = WatchdogService(agent_dir=tmp_path)
    svc.pid_file.write_text("99")
    _cmdline(monkeypatch, "/usr/bin/sleep\0100")
    assert svc.running is False
    _ensure(svc, tmp_path)
    popen.assert_called_once()


def test_ensure_running_updates_password_without_respawn(tmp_path, popen, monkeypatch):
    svc = WatchdogService(agent_dir=tmp_path)
    svc.pid_file.write_text("99")
    _cmdline(monkeypatch, "python3 /x/watchdog/main.py")
    _ensure(svc, tmp_path, password="old")
    _ensure(svc, tmp_path, password="new")
    popen.assert_not_called()
    assert svc.cred_file.read_text() == "monitoring:new"
    assert svc.cred_file.stat().st_mode & 0o777 == 0o600


def test_stop_kills_and_removes_files(tmp_path, kill, monkeypatch):
    svc = WatchdogService(agent_dir=tmp_path)
    svc.pid_file.write_text("99")
    svc.cred_file.write_text("monitoring:pw")
    _cmdline(monkeypatch, "python3 /x/watchdog/main.py")
    svc.stop()
    kill.assert_called_once_with(99, signal.SIGTERM)
    assert not svc.pid_file.exists()
    assert not svc.cred_file.exists()


def test_stop_without_pid_is_noop(tmp_path, kill):
    svc = WatchdogService(agent_dir=tmp_path)
    svc.stop()
    kill.assert_not_called()
