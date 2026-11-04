# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import sys
from unittest.mock import patch

from common.watchdog import main as watchdog
from common.watchdog.detect import ProbeResult, RouteProbe

ROUTE = "bootstrap_rw"
ROUTE_X = "bootstrap_x_rw"
DESTS = [("mysql-0.example.", 3306)]


def _probe(route: str, result: ProbeResult) -> RouteProbe:
    return RouteProbe(route, result, DESTS, "detail")


def test_tracker_fires_after_threshold():
    tracker = watchdog.StuckTracker()
    stuck = _probe(ROUTE, ProbeResult.STUCK)
    for now in range(0, 60, 10):
        assert tracker.update(stuck, float(now)) is False
    assert tracker.update(stuck, 60.0) is True
    assert tracker.update(stuck, 70.0) is False


def test_tracker_resets_on_ok_and_unknown():
    tracker = watchdog.StuckTracker()
    stuck = _probe(ROUTE, ProbeResult.STUCK)
    for now in range(0, 60, 10):
        assert tracker.update(stuck, float(now)) is False
    assert tracker.update(_probe(ROUTE, ProbeResult.UNKNOWN), 55.0) is False
    for now in range(60, 120, 10):
        assert tracker.update(stuck, float(now)) is False
    assert tracker.update(stuck, 120.0) is True

    tracker = watchdog.StuckTracker()
    tracker.update(stuck, 0.0)
    assert tracker.update(_probe(ROUTE, ProbeResult.OK), 30.0) is False
    assert tracker.update(stuck, 60.0) is False


def test_tracker_is_per_route():
    tracker = watchdog.StuckTracker()
    stuck = _probe(ROUTE, ProbeResult.STUCK)
    tracker.update(stuck, 0.0)
    assert tracker.update(_probe(ROUTE_X, ProbeResult.OK), 30.0) is False
    assert tracker.update(stuck, 60.0) is True


def _creds(tmp_path):
    cred_file = tmp_path / "cred"
    cred_file.write_text("monitoring:secret")
    return cred_file


def test_run_once_dispatches_once_when_both_routes_due(tmp_path):
    charm_dir = tmp_path / "charm"
    tracker = watchdog.StuckTracker()
    for route in (ROUTE, ROUTE_X):
        tracker.update(_probe(route, ProbeResult.STUCK), 0.0)
    with (
        patch.object(
            watchdog.detect,
            "probe_route",
            side_effect=lambda route, **_: _probe(route, ProbeResult.STUCK),
        ),
        patch.object(watchdog.subprocess, "run") as run,
    ):
        dispatched = watchdog.run_once(
            tracker,
            unit_name="mysql-router-k8s/0",
            charm_dir=charm_dir,
            cred_file=_creds(tmp_path),
            now=60.0,
        )
    assert dispatched is True
    assert run.call_count == 1
    assert run.call_args.args[0] == [
        "/usr/bin/juju-exec",
        "-u",
        "mysql-router-k8s/0",
        "JUJU_DISPATCH_PATH=hooks/rw_listener_stuck",
        f"{charm_dir}/dispatch",
    ]


def test_run_once_noop_without_credentials(tmp_path):
    with patch.object(watchdog.detect, "probe_route") as probe:
        dispatched = watchdog.run_once(
            watchdog.StuckTracker(),
            unit_name="u/0",
            charm_dir=tmp_path,
            cred_file=tmp_path / "missing",
            now=0.0,
        )
    assert dispatched is False
    probe.assert_not_called()


def test_run_once_probe_exception_resets_route(tmp_path):
    tracker = watchdog.StuckTracker()
    for route in (ROUTE, ROUTE_X):
        tracker.update(_probe(route, ProbeResult.STUCK), 0.0)
    with (
        patch.object(watchdog.detect, "probe_route", side_effect=ValueError("boom")),
        patch.object(watchdog.subprocess, "run") as run,
    ):
        dispatched = watchdog.run_once(
            tracker,
            unit_name="u/0",
            charm_dir=tmp_path,
            cred_file=_creds(tmp_path),
            now=60.0,
        )
    assert dispatched is False
    run.assert_not_called()
    # timers were reset: stuck again at t=60 does not fire
    assert tracker.update(_probe(ROUTE, ProbeResult.STUCK), 60.0) is False


def test_main_exits_when_charm_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["main.py", "u/0", str(tmp_path / "nope"), str(tmp_path / "cred")]
    )
    with patch.object(watchdog.time, "sleep") as sleep:
        assert watchdog.main() is None
    sleep.assert_not_called()


def test_main_loop_survives_exceptions(tmp_path, monkeypatch):
    charm_dir = tmp_path / "charm"
    charm_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["main.py", "u/0", str(charm_dir), str(tmp_path / "cred")])

    def fake_sleep(_):
        if fake_sleep.calls:
            charm_dir.rmdir()
        fake_sleep.calls += 1

    fake_sleep.calls = 0
    with (
        patch.object(watchdog, "run_once", side_effect=RuntimeError("boom")) as run_once,
        patch.object(watchdog.time, "sleep", side_effect=fake_sleep),
    ):
        watchdog.main()
    assert run_once.call_count == 2
