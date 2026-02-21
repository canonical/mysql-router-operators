# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import itertools
import json
import logging
import pathlib
import subprocess
import tempfile
from collections.abc import Callable

import jubilant_backports
import mysql.connector
import tenacity
import yaml
from jubilant_backports import Juju
from jubilant_backports.statustypes import Status
from mysql.connector.errors import (
    DatabaseError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)

from .connector import MySQLConnector
from .juju_ import run_action

logger = logging.getLogger(__name__)

CONTINUOUS_WRITES_DATABASE_NAME = "continuous_writes"
CONTINUOUS_WRITES_TABLE_NAME = "data"

MYSQL_DEFAULT_APP_NAME = "mysql-k8s"
MYSQL_ROUTER_DEFAULT_APP_NAME = "mysql-router-k8s"
APPLICATION_DEFAULT_APP_NAME = "mysql-test-app"

SERVER_CONFIG_USERNAME = "serverconfig"
CONTAINER_NAME = "mysql-router"
LOGROTATE_EXECUTOR_SERVICE = "logrotate_executor"

JujuModelStatusFn = Callable[[Status], bool]
JujuAppsStatusFn = Callable[[Status, str], bool]


def execute_queries_against_unit(
    unit_address: str,
    username: str,
    password: str,
    queries: list[str],
    commit: bool = False,
) -> list:
    """Execute given MySQL queries on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the queries on
        username: The MySQL username
        password: The MySQL password
        queries: A list of queries to execute
        commit: A keyword arg indicating whether there are any writes queries

    Returns:
        A list of rows that were potentially queried
    """
    connection = mysql.connector.connect(
        host=unit_address,
        user=username,
        password=password,
    )
    cursor = connection.cursor()

    for query in queries:
        cursor.execute(query)

    if commit:
        connection.commit()

    output = list(itertools.chain(*cursor.fetchall()))

    cursor.close()
    connection.close()

    return output


def get_server_config_credentials(juju: Juju, unit_name: str) -> dict:
    """Helper to run an action to retrieve server config credentials.

    Args:
        juju: Jubilant Juju instance
        unit_name: The juju unit name on which to run the get-password action for server-config credentials

    Returns:
        A dictionary with the server config username and password
    """
    return run_action(juju, unit_name, "get-password", username=SERVER_CONFIG_USERNAME)


def get_inserted_data_by_application(juju: Juju, unit_name: str) -> str | None:
    """Helper to run an action to retrieve inserted data by the application.

    Args:
        juju: Jubilant Juju instance
        unit_name: The juju unit name on which to run the get-inserted-data action

    Returns:
        A string representing the inserted data
    """
    return run_action(juju, unit_name, "get-inserted-data").get("data")


def get_credentials(juju: Juju, unit_name: str) -> dict:
    """Helper to run an action on data-integrator to get credentials.

    Args:
        juju: Jubilant Juju instance
        unit_name: The data-integrator unit name to run action against

    Returns:
        A dictionary with the credentials
    """
    return run_action(juju, unit_name, "get-credentials")


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
    return status.apps[app_name].units[unit_name].address


def scale_application(
    juju: Juju, application_name: str, desired_count: int, wait: bool = True
) -> None:
    """Scale a given application to the desired unit count.

    Args:
        juju: Jubilant Juju instance
        application_name: The name of the application
        desired_count: The number of units to scale to
        wait: Boolean indicating whether to wait until units
            reach desired count
    """
    juju.cli("scale-application", application_name, str(desired_count))

    if desired_count > 0 and wait:
        juju.wait(
            ready=lambda status: all((
                wait_for_apps_status(jubilant_backports.all_active, application_name)(status),
                len(status.apps[application_name].units) == desired_count,
            )),
            timeout=15 * 60,
        )


def delete_file_or_directory_in_unit(
    juju: Juju, unit_name: str, path: str, container_name: str = CONTAINER_NAME
) -> None:
    """Delete a file in the provided unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name unit on which to delete the file from
        container_name: The name of the container where the file or directory is
        path: The path of file or directory to delete
    """
    if path.strip() in ["/", "."]:
        return

    juju.ssh(unit_name, "find", path, "-maxdepth", "1", "-delete", container=container_name)


def get_process_pid(juju: Juju, unit_name: str, container_name: str, process: str) -> int:
    """Return the pid of a process running in a given unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit
        container_name: The name of the container to get the process pid from
        process: The process name to search for
    Returns:
        A integer for the process id
    """
    try:
        result = juju.ssh(unit_name, "pgrep", "-x", process)
        pid = int(result.strip())
        return pid
    except Exception:
        return None


def write_content_to_file_in_unit(
    juju: Juju,
    unit_name: str,
    path: str,
    content: str,
    container_name: str = CONTAINER_NAME,
) -> None:
    """Write content to the file in the provided unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The unit name in which to write to file in
        path: The path at which to write the content to
        content: The content to write to the file
        container_name: The container where to write the file
    """
    pod_name = unit_name.replace("/", "-")
    model_name = juju.model or ""

    with tempfile.NamedTemporaryFile(mode="w", dir=pathlib.Path.home()) as temp_file:
        temp_file.write(content)
        temp_file.flush()

        subprocess.run(
            [
                "microk8s.kubectl",
                "cp",
                "-n",
                model_name,
                "-c",
                container_name,
                temp_file.name,
                f"{pod_name}:{path}",
            ],
            check=True,
        )


def read_contents_from_file_in_unit(
    juju: Juju, unit_name: str, path: str, container_name: str = CONTAINER_NAME
) -> str:
    """Read contents from file in the provided unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The unit name in which to read file from
        path: The path from which to read content from
        container_name: The container where the file exists

    Returns:
        the contents of the file
    """
    pod_name = unit_name.replace("/", "-")
    model_name = juju.model or ""

    with tempfile.NamedTemporaryFile(mode="r+", dir=pathlib.Path.home()) as temp_file:
        subprocess.run(
            [
                "microk8s.kubectl",
                "cp",
                "-n",
                model_name,
                "-c",
                container_name,
                f"{pod_name}:{path}",
                temp_file.name,
            ],
            check=True,
        )

        temp_file.seek(0)

        contents = ""
        for line in temp_file:
            contents += line
            contents += "\n"

    return contents


def ls_la_in_unit(
    juju: Juju, unit_name: str, directory: str, container_name: str = CONTAINER_NAME
) -> list[str]:
    """Returns the output of ls -la in unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of unit in which to run ls -la
        directory: The directory from which to run ls -la
        container_name: The container where to run ls -la

    Returns:
        a list of files returned by ls -la
    """
    output = juju.ssh(unit_name, "ls", "-la", directory, container=container_name)

    ls_output = output.split("\n")[1:]

    return [
        line.strip("\r")
        for line in ls_output
        if len(line.strip()) > 0 and line.split()[-1] not in [".", ".."]
    ]


def stop_running_log_rotate_executor(juju: Juju, unit_name: str):
    """Stop running the log rotate executor script.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit to be tested
    """
    # send KILL signal to log rotate executor, which trigger shutdown process
    juju.ssh(unit_name, "pebble", "stop", LOGROTATE_EXECUTOR_SERVICE, container=CONTAINER_NAME)


def stop_running_flush_mysqlrouter_job(juju: Juju, unit_name: str) -> None:
    """Stop running any logrotate jobs that may have been triggered by cron.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit to be tested
    """
    # send KILL signal to log rotate process, which trigger shutdown process
    juju.ssh(
        command="pkill -9 logrotate || exit 0",
        target=unit_name,
        container=CONTAINER_NAME,
    )

    # hold execution until process is stopped
    for attempt in tenacity.Retrying(
        reraise=True, stop=tenacity.stop_after_attempt(45), wait=tenacity.wait_fixed(2)
    ):
        with attempt:
            if get_process_pid(juju, unit_name, CONTAINER_NAME, "logrotate"):
                raise Exception("Failed to stop the flush_mysql_logs logrotate process.")


def rotate_mysqlrouter_logs(juju: Juju, unit_name: str) -> None:
    """Dispatch the custom event to run logrotate.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit to be tested
    """
    pod_label = unit_name.replace("/", "-")
    model_name = juju.model or ""

    subprocess.run(
        [
            "microk8s.kubectl",
            "exec",
            "-n",
            model_name,
            "-it",
            pod_label,
            "--container",
            CONTAINER_NAME,
            "--",
            "su",
            "-",
            "mysql",
            "-c",
            "logrotate -f -s /tmp/logrotate.status /etc/logrotate.d/flush_mysqlrouter_logs",
        ],
        check=True,
    )


@tenacity.retry(stop=tenacity.stop_after_attempt(8), wait=tenacity.wait_fixed(15), reraise=True)
def is_connection_possible(credentials: dict, **extra_opts) -> bool:
    """Test a connection to a MySQL server.

    Args:
        credentials: A dictionary with the credentials to test
        extra_opts: extra options for mysql connection
    """
    config = {
        "user": credentials["username"],
        "password": credentials["password"],
        "host": credentials["host"],
        "raise_on_warnings": False,
        "connection_timeout": 10,
        **extra_opts,
    }
    try:
        with MySQLConnector(config) as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1
    except (DatabaseError, InterfaceError, OperationalError, ProgrammingError) as e:
        # Errors raised when the connection is not possible
        logger.error(e)
        return False


def get_tls_ca(
    juju: Juju,
    unit_name: str,
) -> str:
    """Returns the TLS CA used by the unit.

    Args:
        juju: Jubilant Juju instance
        unit_name: The name of the unit

    Returns:
        TLS CA or an empty string if there is no CA.
    """
    raw_data = juju.cli("show-unit", unit_name)
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [
        v for v in data[unit_name]["relation-info"] if v["endpoint"] == "certificates"
    ]
    if len(relation_data) == 0:
        return ""
    return json.loads(relation_data[0]["application-data"]["certificates"])[0].get("ca")


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
        container=CONTAINER_NAME,
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


def get_juju_status(model_name: str) -> str:
    """Return the juju status output.

    Args:
        model_name: The model for which to retrieve juju status for
    """
    return subprocess.check_output(["juju", "status", "--model", model_name]).decode("utf-8")


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


def wait_for_unit_status(app_name: str, unit_name: str, unit_status: str) -> JujuModelStatusFn:
    """Returns whether a Juju unit to have a specific status."""
    return lambda status: (
        status.apps[app_name].units[unit_name].workload_status.current == unit_status
    )
