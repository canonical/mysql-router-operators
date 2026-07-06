# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
import pytest
from jubilant_backports import Juju

from ..architecture import architecture
from ..helpers_new import (
    METADATA,
    MINUTE_SECS,
    check_server_writes_increment,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_deploy_edge(juju: Juju, ubuntu_base: str) -> None:
    """Simple test to ensure that mysql, mysqlrouter and application charms deploy."""
    router_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    if architecture == "amd64":
        router_revision = 748
        server_revision = 346
    elif architecture == "arm64":
        router_revision = 747
        server_revision = 348
    else:
        pytest.skip(f"Architecture {architecture} not supported in this test")

    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/stable",
        revision=server_revision,
        config={"profile": "testing"},
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=MYSQL_ROUTER_APP_NAME,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/stable",
        revision=router_revision,
        resources=router_resources,
        num_units=3,
        trust=True,
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
        ready=wait_for_apps_status(jubilant_backports.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )


def test_upgrade_from_v2(juju: Juju, charm: str) -> None:
    """Upgrade mysqlrouter from v2 to v3 while ensuring continuous writes incrementing."""
    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    logging.info("Refresh the charm with local v3 build")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=charm,
        resources={
            "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"],
        },
    )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)
