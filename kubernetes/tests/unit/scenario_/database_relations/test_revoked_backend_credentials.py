# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test that the charm does not crash when the backend database credentials are revoked.

The MySQL charm revokes the backend-database relation user (e.g. during relation teardown or
when the server is scaled to zero and back). On any subsequent event, the charm's MySQL Shell
queries fail with `MySQL Error 1045 (28000): Access denied`. The charm must report a Waiting
status instead of failing the hook—retrying cannot succeed, but crashing blocks the unit.
"""

import collections.abc

import common.container
import ops
import pytest
import scenario
from mysql_shell.executors.errors import ExecutionError

import charm


@pytest.fixture
def revoked_credentials(monkeypatch) -> None:
    """Simulate a revoked backend-database user: every SQL query fails with access denied."""

    def raise_access_denied(self, sql):  # noqa: A002
        raise ExecutionError(
            "Cannot set LC_ALL to locale en_US.UTF-8: No such file or directory\n"
            "MySQL Error 1045 (28000): Access denied for user "
            "'relation-198_9ffe88a151754'@'router-0.router-endpoints.foo.svc.cl' "
            "(using password: YES)"
        )

    monkeypatch.setattr(
        "mysql_shell_contrib.executors.PebbleExecutor.execute_sql", raise_access_denied
    )


@pytest.fixture
def bootstrap_access_denied(monkeypatch) -> None:
    """Simulate the router bootstrap failing with access denied (server error 1045)."""

    def raise_access_denied(self, command, *, timeout=None, input=None):  # noqa: A002
        if "--bootstrap" in command:
            raise common.container.CalledProcessError(
                returncode=1,
                cmd=command,
                output="",
                stderr="Error: Unable to connect to the metadata server: "
                "Error connecting to MySQL server at mysql-primary.foo.svc.cluster.local.:3306: "
                "Access denied for user 'relation-198_9ffe88a151754'@'router-0.router-endpoints"
                ".foo.svc.cl' (using password: YES) (1045)",
            )
        return "null"

    monkeypatch.setattr("rock.Rock._run_command", raise_access_denied)


def _context_and_state(
    complete_requires: scenario.Relation,
) -> tuple[scenario.Context, scenario.State]:
    context = scenario.Context(charm.KubernetesRouterCharm)
    container = scenario.Container("mysql-router", can_connect=True)
    state = scenario.State(
        relations=[
            complete_requires,
            scenario.PeerRelation(endpoint="mysql-router-peers"),
            scenario.PeerRelation(endpoint="refresh-v-three"),
        ],
        containers=[container],
        leader=True,
    )
    return context, state


def test_update_status_revoked_credentials_reports_waiting(
    complete_requires, revoked_credentials
) -> collections.abc.Iterator[None]:
    """On update-status with revoked credentials, the hook must not crash."""
    context, state = _context_and_state(complete_requires)
    output = context.run(state.containers[0].update_status_event, state)
    assert isinstance(output.unit_status, ops.WaitingStatus)


def test_update_status_bootstrap_access_denied_reports_waiting(
    complete_requires, revoked_credentials, bootstrap_access_denied
):
    """Bootstrap failing with 1045 must not crash the hook."""
    context, state = _context_and_state(complete_requires)
    output = context.run(state.containers[0].update_status_event, state)
    assert isinstance(output.unit_status, ops.WaitingStatus)


def test_relation_departed_revoked_credentials_does_not_crash(
    complete_requires, revoked_credentials, bootstrap_access_denied
):
    """On backend-database-relation-departed with revoked credentials, the hook must not crash."""
    context, state = _context_and_state(complete_requires)
    context.run(complete_requires.departed_event, state)
