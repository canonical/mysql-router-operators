# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju

from ..helpers_new import (
    METADATA,
    MINUTE_SECS,
    get_app_leader,
    scale_app_units,
    verify_mysql_test_data,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_database_relation(juju: Juju, charm: str) -> None:
    """Test the database relation."""
    router_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base="ubuntu@26.04",
        channel="8.4/edge",
        config={"profile": "testing"},
        num_units=3,
        trust=True,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base="ubuntu@26.04",
        resources=router_resources,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base="ubuntu@26.04",
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

    # Ensure that the data inserted by sample application is present in the database
    verify_mysql_test_data(juju, MYSQL_SERVER_APP_NAME, "random_data", test_app_data)

    # Ensure that both mysqlrouter and the application can be scaled up
    scale_app_units(juju, MYSQL_ROUTER_APP_NAME, 2)
    scale_app_units(juju, MYSQL_TEST_APP_NAME, 2)

    # Ensure that both mysqlrouter and the application can be scaled down
    scale_app_units(juju, MYSQL_ROUTER_APP_NAME, 1)
    scale_app_units(juju, MYSQL_TEST_APP_NAME, 1)
