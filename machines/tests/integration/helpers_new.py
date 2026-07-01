# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from collections.abc import Callable

import jubilant
import requests
from jubilant import Juju
from jubilant.statustypes import Status
from tenacity import Retrying, retry, stop_after_delay, wait_fixed

from .helpers import execute_queries_against_unit

MINUTE_SECS = 60
TEST_DATABASE_NAME = "continuous_writes"

JujuModelStatusFn = Callable[[Status], bool]
JujuAppsStatusFn = Callable[[Status, str], bool]


def check_router_metrics_endpoint(juju: Juju, app_name: str, unit_name: str) -> bool:
    """Checks whether the MySQL Router metrics endpoint is available."""
    unit_address = get_unit_address(juju, app_name, unit_name)

    try:
        for attempt in Retrying(
            stop=stop_after_delay(2 * MINUTE_SECS),
            wait=wait_fixed(10),
            reraise=True,
        ):
            with attempt:
                response = requests.get(f"http://{unit_address}:9152/metrics", stream=False)
                response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        assert "[Errno 111] Connection refused" in str(e)
        return False
    else:
        assert "mysqlrouter_route_health" in response.text
        return True


def check_server_writes_increment(
    juju: Juju, app_name: str, app_units: list[str] | None = None
) -> None:
    """Ensure that continuous writes is incrementing on all units.

    Also, ensure that all continuous writes up to the max written value is available
    on all units (ensure that no committed data is lost).
    """
    if not app_units:
        app_units = get_app_units(juju, app_name)

    app_primary = get_mysql_primary_unit(juju, app_name, app_units[0])
    app_max_value = get_mysql_max_written_value(juju, app_name, app_primary)

    for unit_name in app_units:
        for attempt in Retrying(
            stop=stop_after_delay(5 * MINUTE_SECS),
            wait=wait_fixed(10),
            reraise=True,
        ):
            with attempt:
                unit_max_value = get_mysql_max_written_value(juju, app_name, unit_name)
                assert unit_max_value > app_max_value, "Writes not incrementing"
                app_max_value = unit_max_value


def get_app_leader(juju: Juju, app_name: str) -> str:
    """Get the leader unit for the given application."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    for name, status in app_status.units.items():
        if status.leader:
            return name

    raise Exception("No leader unit found")


def get_app_units(juju: Juju, app_name: str) -> list[str]:
    """Get the units for the given application."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    return list(app_status.units)


def scale_app_units(juju: Juju, app_name: str, num_units: int) -> None:
    """Scale a given application to a number of units."""
    app_units = get_app_units(juju, app_name)
    app_units_diff = num_units - len(app_units)

    if app_units_diff > 0:
        scale_func = juju.add_unit
    if app_units_diff < 0:
        scale_func = juju.remove_unit
    if app_units_diff == 0:
        return

    scale_func(app_name, num_units=abs(app_units_diff))

    juju.wait(
        ready=lambda status: len(status.apps[app_name].units) == num_units,
        timeout=10 * MINUTE_SECS,
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, app_name),
        timeout=10 * MINUTE_SECS,
    )


def get_unit_address(juju: Juju, app_name: str, unit_name: str) -> str:
    """Get the application unit IP."""
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    for name, status in app_status.units.items():
        if name == unit_name:
            return status.public_address

    raise Exception("No application unit found")


def get_unit_certificate_issuer(juju: Juju, unit_name: str, host: str, port: int) -> str:
    """Get the TLS certificate issuer string."""
    output = juju.ssh(
        command=(
            f"openssl s_client -showcerts -starttls mysql -connect {host}:{port} < /dev/null "
            f"| openssl x509 -text "
            f"| grep Issuer"
        ),
        target=unit_name,
    )

    return output.strip()


@retry(stop=stop_after_delay(5 * MINUTE_SECS), wait=wait_fixed(10), reraise=True)
def get_mysql_cluster_status(juju: Juju, unit: str, cluster_set: bool = False) -> dict:
    """Get the cluster status by running the get-cluster-status action.

    Args:
        juju: The juju instance to use.
        unit: The unit on which to execute the action on
        cluster_set: Whether to get the cluster-set instead (optional)

    Returns:
        A dictionary representing the cluster status
    """
    task = juju.run(
        unit=unit,
        action="get-cluster-status",
        params={"cluster-set": cluster_set},
        wait=5 * MINUTE_SECS,
    )

    status = task.results["status"]
    status = json.loads(status)
    return status


def get_mysql_instance_label(unit_name: str) -> str:
    """Builds a MySQL instance label out of a Juju unit name."""
    return "-".join(unit_name.rsplit("/", 1))


def get_mysql_unit_name(instance_label: str) -> str:
    """Builds a Juju unit name out of a MySQL instance label."""
    return "/".join(instance_label.rsplit("-", 1))


def get_mysql_max_written_value(juju: Juju, app_name: str, unit_name: str) -> int:
    """Retrieve the max written value in the MySQL database.

    Args:
        juju: The Juju model.
        app_name: The application name.
        unit_name: The unit name.
    """
    credentials = get_mysql_server_credentials(juju, unit_name, "charmed-operator")

    output = execute_queries_against_unit(
        get_unit_address(juju, app_name, unit_name),
        credentials["username"],
        credentials["password"],
        ["SELECT MAX(number) FROM `continuous_writes`.`data`;"],
    )
    return output[0]


def get_mysql_primary_unit(juju: Juju, app_name: str, unit_name: str | None = None) -> str:
    """Get the current primary node of the cluster."""
    if unit_name is None:
        unit_name = get_app_leader(juju, app_name)

    mysql_cluster_status = get_mysql_cluster_status(juju, unit_name)
    mysql_cluster_topology = mysql_cluster_status["defaultReplicaSet"]["topology"]

    for label, value in mysql_cluster_topology.items():
        if value["memberRole"] == "PRIMARY":
            return get_mysql_unit_name(label)

    raise Exception("No MySQL primary node found")


def get_mysql_server_credentials(juju: Juju, unit_name: str, username: str) -> dict[str, str]:
    """Helper to run an action to retrieve server config credentials.

    Args:
        juju: The Juju model
        unit_name: The juju unit on which to get the credentials
        username: The username to use

    Returns:
        A dictionary with the server config username and password
    """
    credentials_task = juju.run(
        unit=unit_name,
        action="get-password",
        params={"username": username},
    )

    return credentials_task.results


def verify_mysql_test_data(juju: Juju, app_name: str, table_name: str, value: str) -> None:
    """Verifies data into the MySQL database.

    Args:
        juju: The Juju model.
        app_name: The application name.
        table_name: The database table name.
        value: The value to check against.
    """
    mysql_app_leader = get_app_leader(juju, app_name)
    credentials = get_mysql_server_credentials(juju, mysql_app_leader, "charmed-operator")

    select_queries = [
        f"SELECT data FROM `{TEST_DATABASE_NAME}`.`{table_name}` WHERE data = '{value}'",
    ]

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            output = execute_queries_against_unit(
                get_unit_address(juju, app_name, mysql_app_leader),
                credentials["username"],
                credentials["password"],
                select_queries,
            )
            assert output[0] == value


def wait_for_apps_status(jubilant_status_func: JujuAppsStatusFn, *apps: str) -> JujuModelStatusFn:
    """Waits for Juju agents to be idle, and for applications to reach a certain status.

    Args:
        jubilant_status_func: The Juju apps status function to wait for.
        apps: The applications to wait for.

    Returns:
        Juju model status function.
    """
    return lambda status: all((
        jubilant.all_agents_idle(status, *apps),
        jubilant_status_func(status, *apps),
    ))


def wait_for_unit_status(app_name: str, unit_name: str, unit_status: str) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific status."""
    return lambda status: (
        status.apps[app_name].units[unit_name].workload_status.current == unit_status
    )


def wait_for_unit_message(app_name: str, unit_name: str, unit_message: str) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific message."""
    return lambda status: (
        status.apps[app_name].units[unit_name].workload_status.message == unit_message
    )
