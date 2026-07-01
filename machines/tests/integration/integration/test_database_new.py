# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju

from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    scale_app_units,
    verify_mysql_test_data,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_database_relation(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test the database relation."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
        config={"profile": "testing"},
        num_units=1,
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

    test_app_leader = get_app_leader(juju, MYSQL_TEST_APP_NAME)
    test_app_task = juju.run(test_app_leader, "get-inserted-data")
    test_app_data = test_app_task.results["data"]

    verify_mysql_test_data(juju, MYSQL_SERVER_APP_NAME, "random_data", test_app_data)

    # Ensure that the application can be scaled up
    scale_app_units(juju, MYSQL_TEST_APP_NAME, 2)

    # Ensure that the application can be scaled down
    scale_app_units(juju, MYSQL_TEST_APP_NAME, 1)
