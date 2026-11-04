# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for the RW listener stuck event handler and watchdog lifecycle (BUG#39215253)."""

import unittest.mock

import charm_refresh
import common.abstract_charm
import ops
import pytest
import scenario
from common.watchdog import detect

import charm

from ..conftest import _MockRefresh

_UNIT_NAME = "mysql-router-k8s/0"


@pytest.fixture
def complete_requires() -> scenario.Relation:
    return scenario.Relation(
        endpoint="backend-database",
        remote_app_data={
            "endpoints": "mysql-k8s-primary:5432",
            "password": "Dy0k2UTfyNt2B13cfe412K7YGs07S4U7",
            "username": "relation-68",
        },
    )


@pytest.fixture
def router_enabled(monkeypatch):
    monkeypatch.setattr("rock.Rock.mysql_router_service_enabled", property(lambda _: True))


@pytest.fixture
def restart(monkeypatch) -> unittest.mock.MagicMock:
    mock = unittest.mock.MagicMock()
    monkeypatch.setattr("common.workload.RunningWorkload.restart_stuck_router", mock)
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
            route=route,
            result=result,
            destinations=[("mysql-k8s-0.mysql-k8s-endpoints", 3306)],
            detail="",
        )

    return unittest.mock.MagicMock(side_effect=probe_route)


def _state(*relations: scenario.Relation) -> scenario.State:
    return scenario.State(
        relations=[
            *relations,
            scenario.PeerRelation(endpoint="mysql-router-peers"),
            scenario.PeerRelation(endpoint="refresh-v-three"),
        ],
        containers=[scenario.Container("mysql-router", can_connect=True)],
        leader=True,
    )


def _emit_stuck(context: scenario.Context, state: scenario.State) -> scenario.State:
    return context.run(scenario.Event("rw_listener_stuck"), state)


def test_stuck_route_restarts_router(monkeypatch, complete_requires, router_enabled, restart):
    probe = _probe(detect.ProbeResult.STUCK)
    monkeypatch.setattr("common.watchdog.detect.probe_route", probe)
    context = scenario.Context(charm.KubernetesRouterCharm)
    _emit_stuck(context, _state(complete_requires))
    restart.assert_called_once()
    assert probe.call_args.kwargs["username"] == "monitoring"
    assert any(
        log.level == "WARNING"
        and "route bootstrap_rw listener closed for ~60s while destinations" in log.message
        and "BUG#39215253" in log.message
        for log in context.juju_log
    )


def test_recovered_route_does_not_restart(monkeypatch, complete_requires, router_enabled, restart):
    probe = _probe(detect.ProbeResult.OK)
    monkeypatch.setattr("common.watchdog.detect.probe_route", probe)
    context = scenario.Context(charm.KubernetesRouterCharm)
    _emit_stuck(context, _state(complete_requires))
    restart.assert_not_called()
    assert probe.call_count == len(detect.ROUTES)
    assert any(
        "no route is stuck anymore" in log.message and log.level == "INFO"
        for log in context.juju_log
    )


def test_probe_exception_does_not_restart(monkeypatch, complete_requires, router_enabled, restart):
    monkeypatch.setattr(
        "common.watchdog.detect.probe_route",
        unittest.mock.MagicMock(side_effect=OSError("boom")),
    )
    context = scenario.Context(charm.KubernetesRouterCharm)
    _emit_stuck(context, _state(complete_requires))
    restart.assert_not_called()


def test_no_restart_during_refresh(monkeypatch, complete_requires, router_enabled, restart):
    probe = _probe(detect.ProbeResult.STUCK)
    monkeypatch.setattr("common.watchdog.detect.probe_route", probe)
    monkeypatch.setattr(_MockRefresh, "in_progress", True)
    context = scenario.Context(charm.KubernetesRouterCharm)
    _emit_stuck(context, _state(complete_requires))
    restart.assert_not_called()
    probe.assert_not_called()


def test_no_restart_without_running_workload(monkeypatch, router_enabled, restart):
    probe = _probe(detect.ProbeResult.STUCK)
    monkeypatch.setattr("common.watchdog.detect.probe_route", probe)
    context = scenario.Context(charm.KubernetesRouterCharm)
    _emit_stuck(context, _state())
    restart.assert_not_called()
    probe.assert_not_called()


def test_reconcile_does_not_handle_custom_event(monkeypatch, complete_requires, router_enabled):
    # `@trace_charm` wraps `reconcile` on the substrate charm class, so patching it is unreliable.
    # Detect a real `reconcile` call through its log messages instead.
    monkeypatch.setattr("common.watchdog.detect.probe_route", _probe(detect.ProbeResult.OK))
    context = scenario.Context(charm.KubernetesRouterCharm)
    _emit_stuck(context, _state(complete_requires))
    messages = [log.message for log in context.juju_log]
    assert any("no route is stuck anymore" in message for message in messages)
    assert not any(
        "State of reconcile" in message or "Reconcile not allowed" in message
        for message in messages
    )


def test_reconcile_starts_watchdog_when_router_enabled(
    complete_requires, router_enabled, watchdog
):
    context = scenario.Context(charm.KubernetesRouterCharm)
    context.run(scenario.Event("update_status"), _state(complete_requires))
    watchdog.ensure_running.assert_called_once()
    kwargs = watchdog.ensure_running.call_args.kwargs
    assert kwargs["unit_name"] == _UNIT_NAME
    assert kwargs["username"] == "monitoring"
    assert kwargs["password"]
    watchdog.stop.assert_not_called()


def test_reconcile_stops_watchdog_without_router(watchdog):
    context = scenario.Context(charm.KubernetesRouterCharm)
    context.run(scenario.Event("update_status"), _state())
    watchdog.stop.assert_called_once()
    watchdog.ensure_running.assert_not_called()


def test_reconcile_stops_watchdog_during_refresh(
    monkeypatch, complete_requires, router_enabled, watchdog
):
    monkeypatch.setattr(_MockRefresh, "in_progress", True)
    context = scenario.Context(charm.KubernetesRouterCharm)
    context.run(scenario.Event("update_status"), _state(complete_requires))
    watchdog.stop.assert_called_once()
    watchdog.ensure_running.assert_not_called()


def test_upgrade_charm_restarts_watchdog(complete_requires, router_enabled, watchdog):
    context = scenario.Context(charm.KubernetesRouterCharm)
    context.run(scenario.Event("upgrade_charm"), _state(complete_requires))
    assert [call[0] for call in watchdog.mock_calls] == ["stop", "ensure_running"]


def test_watchdog_error_does_not_fail_hook(complete_requires, router_enabled, watchdog):
    watchdog.ensure_running.side_effect = OSError("cannot spawn")
    context = scenario.Context(charm.KubernetesRouterCharm)
    context.run(scenario.Event("update_status"), _state(complete_requires))
    assert any(
        log.level == "WARNING" and "watchdog" in log.message.lower() for log in context.juju_log
    )


def test_charm_events_include_rw_listener_stuck():
    assert isinstance(charm.KubernetesRouterCharm.on, common.abstract_charm.RouterCharmEvents)
    assert issubclass(common.abstract_charm.RWListenerStuckEvent, ops.EventBase)


def test_unit_tearing_down_stops_watchdog(monkeypatch, watchdog):
    def raise_tearing_down(*_, **__):
        raise charm_refresh.UnitTearingDown

    monkeypatch.setattr("charm_refresh.Kubernetes", raise_tearing_down)
    context = scenario.Context(charm.KubernetesRouterCharm)
    context.run(scenario.Event("update_status"), _state())
    watchdog.stop.assert_called_once()
    watchdog.ensure_running.assert_not_called()
