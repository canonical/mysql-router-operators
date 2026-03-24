# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time
from pathlib import Path

import jubilant_backports
import pytest
import yaml
from jubilant_backports import Juju

from .architecture import architecture
from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    ensure_all_units_continuous_writes_incrementing,
)

logger = logging.getLogger(__name__)

TIMEOUT = 20 * 60
UPGRADE_TIMEOUT = 15 * 60

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
RESOURCES = {"mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]}


@pytest.mark.abort_on_fail
def test_deploy_v2(juju: Juju, ubuntu_base) -> None:
    """Deploy v2 charms and test application."""
    logger.info("Deploying all applications")

    if architecture == "amd64":
        router_rev = 748
        mysql_rev = 346
    elif architecture == "arm64":
        router_rev = 747
        mysql_rev = 348
    else:
        pytest.skip(f"Architecture {architecture} not supported in this test")

    juju.deploy(
        "mysql-k8s",
        channel="8.0/stable",
        revision=mysql_rev,
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
        base=ubuntu_base,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        "mysql-router-k8s",
        channel="8.0/stable",
        revision=router_rev,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=3,
        trust=True,
    )
    juju.deploy(
        "mysql-test-app",
        channel="latest/edge",
        app=APPLICATION_APP_NAME,
        base=ubuntu_base,
        num_units=1,
        constraints={"arch": architecture},
    )

    logger.info(f"Relating {MYSQL_ROUTER_APP_NAME} to {MYSQL_APP_NAME} and {APPLICATION_APP_NAME}")

    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
    juju.integrate(f"{APPLICATION_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:database")

    logger.info("Waiting for applications to become active")
    juju.wait(
        lambda status: jubilant_backports.all_active(status, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
def test_upgrade_from_v2(juju: Juju, charm) -> None:
    """Upgrade mysqlrouter from v2 to v3 while ensuring continuous writes incrementing."""
    ensure_all_units_continuous_writes_incrementing(juju)

    logger.info("Refresh the charm with local v3 build")
    juju.cli(
        "refresh",
        MYSQL_ROUTER_APP_NAME,
        "--path",
        str(charm),
        "--resource",
        f"mysql-router-image={RESOURCES['mysql-router-image']}",
    )

    logger.info("Block until router become active")
    # sleep to ensure that active status from before re-refresh does not affect below check
    time.sleep(15)

    juju.wait(
        lambda status: jubilant_backports.all_active(status, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )

    logger.info("Ensure continuous writes after upgrade")
    ensure_all_units_continuous_writes_incrementing(juju)
