# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import itertools
import logging
import tempfile
from collections.abc import Callable, Iterable

import jubilant_backports
import tenacity
from jubilant_backports import Juju
from jubilant_backports.statustypes import Status

from .connector import MySQLConnector
from .juju_ import run_action

logger = logging.getLogger(__name__)

CONTINUOUS_WRITES_DATABASE_NAME = "continuous_writes"
CONTINUOUS_WRITES_TABLE_NAME = "data"

MYSQL_DEFAULT_APP_NAME = "mysql"
MYSQL_ROUTER_DEFAULT_APP_NAME = "mysql-router"
APPLICATION_DEFAULT_APP_NAME = "mysql-test-app"

JujuModelStatusFn = Callable[[Status], bool]
JujuAppsStatusFn = Callable[[Status, str], bool]


def get_server_config_credentials(juju: Juju, unit_name: str) -> dict:
    """Helper to run an action to retrieve server config credentials from mysql unit.

    Must be run with a mysql unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The juju unit name on which to run the get-password action for server-config credentials

    Returns:
        A dictionary with the server config username and password
    """
    return run_action(juju, unit_name, "get-password", username="serverconfig")


def get_inserted_data_by_application(juju: Juju, unit_name: str) -> str | None:
    """Helper to run an action to retrieve inserted data by the application.

    Args:
        juju: Jubilant Juju instance
        unit_name: The juju unit name on which to run the get-inserted-data action

    Returns:
        A string representing the inserted data
    """
    return run_action(juju, unit_name, "get-inserted-data").get("data")


def execute_queries_against_unit(
    unit_address: str,
    username: str,
    password: str,
    queries: Iterable[str],
    port: int = 3306,
    commit: bool = False,
) -> list:
    """Execute given MySQL queries on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the queries on
        username: The MySQL username
        password: The MySQL password
        queries: A list of queries to execute
        port: The port to connect to in order to execute queries
        commit: A keyword arg indicating whether there are any writes queries

    Returns:
        A list of rows that were potentially queried
    """
    config = {
        "user": username,
        "password": password,
        "host": unit_address,
        "port": port,
        "raise_on_warnings": False,
    }

    with MySQLConnector(config, commit) as cursor:
        for query in queries:
            cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))

    return output


def get_process_pid(juju: Juju, unit_name: str, process: str) -> int | None:
    """Return the pid of a process running in a given unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit
        process: The process name to search for
    Returns:
        A integer for the process id
    """
    try:
        result = juju.ssh(unit_name, f"pgrep -x {process}")
        pid = int(result.strip())
        return pid
    except Exception:
        pass


def delete_file_or_directory_in_unit(juju: Juju, unit_name: str, path: str) -> None:
    """Delete a file in the provided unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name unit on which to delete the file from
        path: The path of file or directory to delete
    """
    if path.strip() in ["/", "."]:
        return

    juju.ssh(
        unit_name,
        "sudo",
        "find",
        path,
        "-maxdepth",
        "1",
        "-delete",
    )


def write_content_to_file_in_unit(juju: Juju, unit_name: str, path: str, content: str) -> None:
    """Write content to the file in the provided unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The unit name in which to write to file in
        path: The path at which to write the content to
        content: The content to write to the file.
    """
    with tempfile.NamedTemporaryFile(mode="w") as temp_file:
        temp_file.write(content)
        temp_file.flush()

        # Use juju scp to copy file to unit
        juju.scp(temp_file.name, f"{unit_name}:/tmp/file")

    juju.ssh(unit_name, "sudo", "mv", "/tmp/file", path)
    juju.ssh(unit_name, "sudo", "chown", "snap_daemon:snap_daemon", path)


def read_contents_from_file_in_unit(juju: Juju, unit_name: str, path: str) -> str:
    """Read contents from file in the provided unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The unit name in which to read file from
        path: The path from which to read content from

    Returns:
        the contents of the file
    """
    juju.ssh(unit_name, "sudo", "cp", path, "/tmp/file")
    juju.ssh(unit_name, "sudo", "chown", "ubuntu:ubuntu", "/tmp/file")

    with tempfile.NamedTemporaryFile(mode="r+") as temp_file:
        # Use juju scp to copy file from unit
        juju.scp(f"{unit_name}:/tmp/file", temp_file.name)

        temp_file.seek(0)

        contents = ""
        for line in temp_file:
            contents += line
            contents += "\n"

    juju.ssh(unit_name, "sudo", "rm", "/tmp/file")

    return contents


def ls_la_in_unit(juju: Juju, unit_name: str, directory: str) -> list[str]:
    """Returns the output of ls -la in unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of unit in which to run ls -la
        directory: The directory from which to run ls -la

    Returns:
        a list of files returned by ls -la
    """
    output = juju.ssh(unit_name, "sudo", "ls", "-la", directory)

    ls_output = output.split("\n")[1:]

    return [
        line.strip("\r")
        for line in ls_output
        if len(line.strip()) > 0 and line.split()[-1] not in [".", ".."]
    ]


def stop_running_flush_mysqlrouter_cronjobs(juju: Juju, unit_name: str) -> None:
    """Stop running any logrotate jobs that may have been triggered by cron.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit to be tested
    """
    juju.ssh(
        command="sudo pkill -9 logrotate || exit 0",
        target=unit_name,
    )

    # hold execution until process is stopped
    for attempt in tenacity.Retrying(
        reraise=True, stop=tenacity.stop_after_attempt(45), wait=tenacity.wait_fixed(2)
    ):
        with attempt:
            if get_process_pid(juju, unit_name, "logrotate"):
                raise Exception("Failed to stop the flush_mysql_logs logrotate process")


def get_tls_certificate_issuer(
    juju: Juju,
    unit_name: str,
    socket: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> str:
    """Get TLS certificate issuer.

    Args:
        juju: Jubilant Juju instance
        unit_name: Name of the unit
        socket: Unix socket path
        host: Host address
        port: Port number

    Returns:
        Certificate issuer string
    """
    connect_args = f"-unix {socket}" if socket else f"-connect {host}:{port}"
    issuer = juju.ssh(
        unit_name,
        f"openssl s_client -showcerts -starttls mysql {connect_args} < /dev/null | openssl x509 -text | grep Issuer",
    )
    return issuer


def get_application_name(juju: Juju, application_name_substring: str) -> str:
    """Returns the name of the application with the provided application name.

    This enables us to retrieve the name of the deployed application in an existing model.

    Note: if multiple applications with the application name exist,
    the first one found will be returned.

    Args:
        juju: Jubilant Juju instance
        application_name_substring: Application name substring to search for

    Returns:
        Application name or empty string if not found
    """
    status = juju.status()
    for application in status.apps:
        if application_name_substring == application:
            return application

    return ""


@tenacity.retry(stop=tenacity.stop_after_attempt(30), wait=tenacity.wait_fixed(5), reraise=True)
def get_primary_unit(
    juju: Juju,
    unit_name: str,
    app_name: str,
) -> str:
    """Helper to retrieve the primary unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: A unit name on which to run dba.get_cluster().status() on
        app_name: The name of the test application

    Returns:
        A juju unit name that is a MySQL primary
    """
    status = juju.status()
    unit_names = list(status.apps[app_name].units.keys())
    results = run_action(juju, unit_name, "get-cluster-status")

    primary_unit = None
    for k, v in results["status"]["defaultreplicaset"]["topology"].items():
        if v["memberrole"] == "primary":
            unit_name = f"{app_name}/{k.split('-')[-1]}"
            if unit_name in unit_names:
                primary_unit = unit_name
                break

    if not primary_unit:
        raise ValueError("Unable to find primary unit")
    return primary_unit


def get_leader_unit(juju: Juju, app_name: str) -> str | None:
    """Get the leader unit of a given application.

    Args:
        juju: Jubilant Juju instance
        app_name: The name of the application

    Returns:
        Unit name of the leader or None if not found
    """
    model_status = juju.status()
    app_status = model_status.apps[app_name]
    for name, status in app_status.units.items():
        if status.leader:
            return name

    return None


def get_primary_unit_wrapper(juju: Juju, app_name: str, unit_excluded: str | None = None) -> str:
    """Wrapper for getting primary.

    Args:
        juju: Jubilant Juju instance
        app_name: The name of the application
        unit_excluded: excluded unit name to run command on
    Returns:
        The primary unit name
    """
    logger.info("Retrieving primary unit")
    status = juju.status()
    unit_names = list(status.apps[app_name].units.keys())
    if unit_excluded:
        # if defined, exclude unit from available unit to run command on
        # useful when the workload is stopped on unit
        available_units = [u for u in unit_names if u != unit_excluded]
        unit_name = available_units[0] if available_units else unit_names[0]
    else:
        unit_name = unit_names[0]

    primary_unit = get_primary_unit(juju, unit_name, app_name)

    return primary_unit


def get_unit_address(juju: Juju, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    status = juju.status()
    app_name = unit_name.split("/")[0]
    return status.apps[app_name].units[unit_name].public_address


def get_max_written_value_in_database(juju: Juju, unit_name: str, credentials: dict) -> int:
    """Retrieve the max written value in the MySQL database.

    Args:
        juju: Jubilant Juju instance
        unit_name: The MySQL unit name on which to execute queries on
        credentials: Database credentials to use
    """
    unit_address = get_unit_address(juju, unit_name)

    select_max_written_value_sql = [
        f"SELECT MAX(number) FROM `{CONTINUOUS_WRITES_DATABASE_NAME}`.`{CONTINUOUS_WRITES_TABLE_NAME}`;"
    ]

    output = execute_queries_against_unit(
        unit_address,
        credentials["username"],
        credentials["password"],
        select_max_written_value_sql,
    )

    return output[0]


def ensure_all_units_continuous_writes_incrementing(
    juju: Juju, mysql_unit_names: list[str] | None = None
) -> None:
    """Ensure that continuous writes is incrementing on all units.

    Also, ensure that all continuous writes up to the max written value is available
    on all units (ensure that no committed data is lost).
    """
    logger.info("Ensure continuous writes are incrementing")

    mysql_application_name = get_application_name(juju, MYSQL_DEFAULT_APP_NAME)

    if not mysql_unit_names:
        status = juju.status()
        mysql_unit_names = list(status.apps[mysql_application_name].units.keys())

    primary = get_primary_unit_wrapper(juju, mysql_application_name)

    server_config_credentials = get_server_config_credentials(juju, mysql_unit_names[0])

    last_max_written_value = get_max_written_value_in_database(
        juju, primary, server_config_credentials
    )

    select_all_continuous_writes_sql = [
        f"SELECT * FROM `{CONTINUOUS_WRITES_DATABASE_NAME}`.`{CONTINUOUS_WRITES_TABLE_NAME}`"
    ]

    for unit_name in mysql_unit_names:
        for attempt in tenacity.Retrying(
            reraise=True, stop=tenacity.stop_after_delay(5 * 60), wait=tenacity.wait_fixed(10)
        ):
            with attempt:
                # ensure that all units are up to date (including the previous primary)
                unit_address = get_unit_address(juju, unit_name)

                # ensure the max written value is incrementing (continuous writes is active)
                max_written_value = get_max_written_value_in_database(
                    juju, unit_name, server_config_credentials
                )
                assert max_written_value > last_max_written_value, (
                    "Continuous writes not incrementing"
                )

                # ensure that the unit contains all values up to the max written value
                all_written_values = set(
                    execute_queries_against_unit(
                        unit_address,
                        server_config_credentials["username"],
                        server_config_credentials["password"],
                        select_all_continuous_writes_sql,
                    )
                )
                numbers = set(range(1, max_written_value))
                assert numbers <= all_written_values, (
                    f"Missing numbers in database for unit {unit_name}"
                )

                last_max_written_value = max_written_value


def get_data_integrator_credentials(
    juju: Juju, data_integrator_app_name: str, avoid_unit: str | None = None
) -> dict:
    """Helper to get the credentials from the deployed data integrator

    Args:
        juju: Jubilant Juju instance
        data_integrator_app_name: Name of the data integrator application
        avoid_unit: Unit name to avoid

    Returns:
        Dictionary with credentials
    """
    status = juju.status()
    data_integrator_unit = None

    for unit_name in status.apps[data_integrator_app_name].units:
        if unit_name != avoid_unit:
            data_integrator_unit = unit_name
            break

    assert data_integrator_unit, "No valid data integrator units found to query creds"

    logger.info(f"Running get-credentials on {data_integrator_unit}")

    result = run_action(juju, data_integrator_unit, "get-credentials")
    assert result["ok"] == "True"
    return result["mysql"]


def get_machine_address(juju: Juju, unit_name: str) -> str:
    """Get the unit's machine's address.

    Args:
        juju: Jubilant Juju instance
        unit_name: Name of the unit

    Returns:
        Machine address
    """
    status = juju.status()
    app_name = unit_name.split("/")[0]
    machine_id = status.apps[app_name].units[unit_name].machine
    machine_status = status.machines.get(machine_id)
    if machine_status and machine_status.dns_name:
        return machine_status.dns_name
    elif machine_status and machine_status.ip_addresses:
        return machine_status.ip_addresses[0]

    assert False, "Unable to find the unit's machine"


def wait_for_apps_status(jubilant_status_func: JujuAppsStatusFn, *apps: str) -> JujuModelStatusFn:
    """Waits for Juju agents to be idle, and for applications to reach a certain status.

    Args:
        jubilant_status_func: The Juju apps status function to wait for.
        apps: The applications to wait for.

    Returns:
        Juju model status function.
    """
    return lambda status: all((
        jubilant_backports.all_agents_idle(status, *apps),
        jubilant_status_func(status, *apps),
    ))


def wait_for_unit_status(
    app_name: str, unit_name: str, unit_status: str, subordinate_of=None
) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific status.

    Args:
        app_name: The name of the application the unit belongs to
        unit_name: The name of the unit to check the status of
        unit_status: The status to check for
        subordinate_of: If the unit is a subordinate, the name of the application it is subordinate
    """
    if subordinate_of:
        return lambda status: (
            status.apps[subordinate_of]
            .units[f"{subordinate_of}/0"]
            .subordinates[unit_name]
            .workload_status.current
            == unit_status
        )
    else:
        return lambda status: (
            status.apps[app_name].units[unit_name].workload_status.current == unit_status
        )


def principal_unit_for_subordinate(
    status: Status, subordinate_unit_name: str, principal_app_name: str
) -> str:
    """Returns the principal unit name for a given subordinate unit.

    Args:
        status: The Juju model status
        subordinate_unit_name: The name of the subordinate unit
        principal_app_name: The name of the principal application
    """
    for unit, unit_status in status.apps[principal_app_name].units.items():
        if subordinate_unit_name in unit_status.subordinates:
            return unit

    raise ValueError(f"Unable to find principal unit for subordinate {subordinate_unit_name}")


def wait_for_unit_message(
    app_name: str,
    unit_name: str,
    unit_message: str,
    subordinate_of=None,
) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific message."""
    if subordinate_of:
        return lambda status: (
            status.apps[subordinate_of]
            .units[principal_unit_for_subordinate(status, unit_name, subordinate_of)]
            .subordinates[unit_name]
            .workload_status.message
            == unit_message
        )
    return lambda status: (
        status.apps[app_name].units[unit_name].workload_status.message == unit_message
    )
