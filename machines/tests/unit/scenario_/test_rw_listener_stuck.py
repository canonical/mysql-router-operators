# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for the RW listener stuck event handler and watchdog lifecycle (BUG#39215253)."""

import unittest.mock

import charm_refresh
import pytest
import scenario
from common.watchdog import detect

import charm

_UNIT_NAME = "mysql-router/0"


@pytest.fixture
def complete_requires() -> scenario.Relation:
    return scenario.Relation(
        endpoint="backend-database",
        remote_app_data={
            "endpoints": "mysql-primary:3306",
            "password": "Dy0k2UTfyNt2B13cfe412K7YGs07S4U7",
            "username": "relation-68",
        },
    )


@pytest.fixture
def router_enabled(monkeypatch):
    monkeypatch.setattr("snap.Snap.mysql_router_service_enabled", property(lambda _: True))


@pytest.fixture
def restart(monkeypatch) -> unittest.mock.MagicMock:
    mock = unittest.mock.MagicMock()
    monkeypatch.setattr("machine_workload.RunningMachineWorkload.restart_stuck_router", mock)
    return mock


@pytest.fixture
def watchdog(monkeypatch) -> unittest.mock.MagicMock:
    parent = unittest.mock.MagicMock()
    monkeypatch.setattr(
        "common.watchdog.service.WatchdogService.ensure_running", parent.ensure_running
    )
    monkeypatch.setattr("common.watchdog.service.WatchdogService.stop", parent.stop)
    return parent


def _probe(result: detect.ProbeResult) -> unittest.mock.MagicMock:
    def probe_route(route, **_):
        return detect.RouteProbe(
            route=route, result=result, destinations=[("10.0.0.1", 3306)], detail=""
        )

    return unittest.mock.MagicMock(side_effect=probe_route)


def _state(*relations: scenario.Relation) -> scenario.State:
    return scenario.State(
        relations=[*relations, scenario.PeerRelation(endpoint="refresh-v-three")],
        leader=True,
    )


def test_stuck_route_restarts_router(monkeypatch, complete_requires, router_enabled, restart):
    monkeypatch.setattr("common.watchdog.detect.probe_route", _probe(detect.ProbeResult.STUCK))
    context = scenario.Context(charm.MachineSubordinateRouterCharm)
    context.run(scenario.Event("rw_listener_stuck"), _state(complete_requires))
    restart.assert_called_once()
    assert any(
        log.level == "WARNING"
        and "route bootstrap_rw listener closed for ~60s while destinations" in log.message
        for log in context.juju_log
    )


def test_recovered_route_does_not_restart(monkeypatch, complete_requires, router_enabled, restart):
    monkeypatch.setattr("common.watchdog.detect.probe_route", _probe(detect.ProbeResult.OK))
    context = scenario.Context(charm.MachineSubordinateRouterCharm)
    context.run(scenario.Event("rw_listener_stuck"), _state(complete_requires))
    restart.assert_not_called()


def test_reconcile_starts_watchdog_when_router_enabled(
    complete_requires, router_enabled, watchdog
):
    context = scenario.Context(charm.MachineSubordinateRouterCharm)
    context.run(scenario.Event("update_status"), _state(complete_requires))
    watchdog.ensure_running.assert_called_once()
    assert watchdog.ensure_running.call_args.kwargs["unit_name"] == _UNIT_NAME
    watchdog.stop.assert_not_called()


def test_upgrade_charm_restarts_watchdog(complete_requires, router_enabled, watchdog):
    context = scenario.Context(charm.MachineSubordinateRouterCharm)
    context.run(scenario.Event("upgrade_charm"), _state(complete_requires))
    assert [call[0] for call in watchdog.mock_calls] == ["stop", "ensure_running"]


def test_unit_tearing_down_stops_watchdog(monkeypatch, watchdog):
    def raise_tearing_down(*_, **__):
        raise charm_refresh.UnitTearingDown

    monkeypatch.setattr("charm_refresh.Machines", raise_tearing_down)
    monkeypatch.setattr("snap.uninstall", lambda: None)
    context = scenario.Context(charm.MachineSubordinateRouterCharm)
    context.run(scenario.Event("update_status"), _state())
    watchdog.stop.assert_called_once()
    watchdog.ensure_running.assert_not_called()
