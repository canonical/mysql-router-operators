# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from contextlib import contextmanager

import jubilant_backports
import pytest
from jubilant_backports import Juju

from .. import architecture, markers
from ..helpers_new import (
    MINUTE_SECS,
    check_server_writes_increment,
    get_app_leader,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


@contextmanager
def continuous_writes(juju: Juju):
    """Starts continuous writes to the MySQL cluster for a test and clear the writes at the end."""
    test_app_leader = get_app_leader(juju, MYSQL_TEST_APP_NAME)

    logging.info("Clearing continuous writes")
    juju.run(test_app_leader, "clear-continuous-writes")
    logging.info("Starting continuous writes")
    juju.run(test_app_leader, "start-continuous-writes")

    yield

    logging.info("Clearing continuous writes")
    juju.run(test_app_leader, "clear-continuous-writes")


@markers.amd64_only
def test_upgrade_from_stable_amd(juju: Juju, charm: str, ubuntu_base: str):
    """Simple test to ensure that all MySQL stable revisions can be upgraded."""
    revision = os.getenv("CHARM_REVISION_AMD64")
    if revision is None:
        pytest.skip(f"No revision for {architecture.architecture} architecture")

    deploy_stable(juju, ubuntu_base, int(revision))

    with continuous_writes(juju):
        upgrade_from_stable(juju, charm)


@markers.arm64_only
def test_upgrade_from_stable_arm(juju: Juju, charm: str, ubuntu_base: str):
    """Simple test to ensure that all MySQL stable revisions can be upgraded."""
    revision = os.getenv("CHARM_REVISION_ARM64")
    if revision is None:
        pytest.skip(f"No revision for {architecture.architecture} architecture")

    deploy_stable(juju, ubuntu_base, int(revision))

    with continuous_writes(juju):
        upgrade_from_stable(juju, charm)


# TODO: add s390x test


def deploy_stable(juju: Juju, ubuntu_base: str, revision: int) -> None:
    """Ensure that the MySQL, MySQL Router and application charms get deployed."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/stable",
        config={"profile": "testing"},
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=MYSQL_ROUTER_APP_NAME,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        channel="dpe/candidate",
        revision=revision,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        num_units=3,
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


def upgrade_from_stable(juju: Juju, charm: str) -> None:
    """Upgrade mysqlrouter while ensuring continuous writes incrementing."""
    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    logging.info("Refresh the charm")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=charm,
    )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)
