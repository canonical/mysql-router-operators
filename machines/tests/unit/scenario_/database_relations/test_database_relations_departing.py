# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test that the charm does not re-bootstrap or contact MySQL on relation-departed.

When the backend-database relation is removed (e.g. via `juju remove-relation`),
the MySQL charm removes the router from the cluster metadata during *its* relation-departed.
On the router's `backend-database-relation-departed`, charm must not re-bootstrap or otherwise contact MySQL:
the relation is being torn down, and contacting MySQL races with the server revoking access.
"""

import common.container
import pytest
import scenario
from mysql_shell.executors.errors import ExecutionError

import charm


def output_state(*, relations: list[scenario.Relation], event: scenario.Event) -> scenario.State:
    context = scenario.Context(charm.MachineSubordinateRouterCharm)
    input_state = scenario.State(
        relations=[*relations, scenario.PeerRelation(endpoint="refresh-v-three")],
        leader=True,
    )
    return context.run(event, input_state)


@pytest.fixture
def bootstrap_calls(monkeypatch) -> list:
    """Spy on mysqlrouter bootstrap commands.

    The global fixture makes the router appear absent from the cluster set (as if
    the MySQL charm had removed it), which is what triggers a re-bootstrap.
    """
    calls = []

    def spy_run_command(self, command, *, timeout=None):  # noqa: A002
        if "--bootstrap" in command:
            calls.append(command)
        return "null"

    monkeypatch.setattr("snap.Snap._run_command", spy_run_command)
    return calls


def test_departing_requires_does_not_rebootstrap(complete_requires, bootstrap_calls):
    """On backend-database-relation-departed the charm must not re-bootstrap."""
    output_state(relations=[complete_requires], event=complete_requires.departed_event)
    assert bootstrap_calls == []


def test_departing_requires_does_not_contact_mysql(complete_requires, monkeypatch):
    """On backend-database-relation-departed the charm must not contact MySQL at any point.

    Mock setup to ensure all MySQL shell calls fail.
    """

    def raise_access_denied(*args, **kwargs):
        raise ExecutionError(
            "MySQL Error 1045 (28000): Access denied for user "
            "'relation-68'@'juju-abc123-4.lxd' (using password: YES)"
        )

    monkeypatch.setattr("common.mysql_shell.Shell.get_routers_in_cluster_set", raise_access_denied)
    monkeypatch.setattr(
        "common.mysql_shell.Shell.get_mysql_router_user_for_unit", raise_access_denied
    )

    def failing_run_command(self, command, *, timeout=None):  # noqa: A002
        if "--bootstrap" in command:
            raise common.container.CalledProcessError(
                returncode=1,
                cmd=command,
                output="",
                stderr="Error: Unable to connect to the metadata server: "
                "MySQL Error 1045 (28000): Access denied for user (1045)",
            )
        return "null"

    monkeypatch.setattr("snap.Snap._run_command", failing_run_command)

    # Must not raise: the hook succeeds because MySQL is never contacted.
    output_state(relations=[complete_requires], event=complete_requires.departed_event)
