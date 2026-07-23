# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import jubilant
import mysql.connector
from jubilant import Juju

from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_mysql_server_credentials,
    get_unit_address,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_deploy_and_relate(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test the database relation."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
        config={"profile": "testing"},
        # MySQL Router 8.4 requires cluster quorum for R/W traffic,
        # because of the unreachable_quorum_allowed_traffic config option
        # (only observable upon process restart)
        num_units=3,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=1,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        num_units=1,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )
    juju.integrate(
        f"{MYSQL_TEST_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )


def test_connection_sharing_disabled(juju: Juju) -> None:
    """Test connection sharing disabled mode."""
    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"connection-sharing": "false"},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    attempts = 10
    conn_ids = fetch_connection_ids(juju, attempts)
    assert len(conn_ids) == attempts


def test_connection_sharing_enabled(juju: Juju) -> None:
    """Test connection sharing enabled mode."""
    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"connection-sharing": "true"},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    attempts = 10
    conn_ids = fetch_connection_ids(juju, attempts)
    assert len(conn_ids) == 1


def fetch_connection_ids(juju: Juju, num_connections: int) -> set:
    """Fetches the connection ids."""
    router_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)
    server_leader = get_app_leader(juju, MYSQL_SERVER_APP_NAME)
    server_creds = get_mysql_server_credentials(juju, server_leader, "charmed-operator")

    connections = []
    connection_ids = []

    # The total sleep seconds across all iterations
    # must be lower than MySQL Router `idle_timeout`.
    # Otherwise, the connections will not be reused.
    for i in range(num_connections):
        connection = mysql.connector.connect(
            user=server_creds["username"],
            password=server_creds["password"],
            host=get_unit_address(juju, MYSQL_ROUTER_APP_NAME, router_leader),
            port=6446,
        )

        cursor = connection.cursor()
        cursor.execute("SELECT CONNECTION_ID();")

        connection_id = cursor.fetchone()[0]
        connection.commit()
        cursor.close()

        connections.append(connection)
        connection_ids.append(connection_id)

        # This sleep time needs to be higher than the value of the
        # MySQL Router `connection_sharing_delay` (defaults to `1`).
        # Otherwise, the connections will not be reused
        time.sleep(2)

    for connection in connections:
        connection.close()

    return set(connection_ids)
