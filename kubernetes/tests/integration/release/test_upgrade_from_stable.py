# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from contextlib import contextmanager, suppress

import jubilant_backports
import pytest
from jubilant_backports import Juju

from .. import architecture, markers
from ..conftest import continuous_writes
from ..helpers_new import (
    METADATA,
    MINUTE_SECS,
    check_server_writes_increment,
    get_app_leader,
    get_app_units,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"
MYSQL_TEST_APP_NAME = "mysql-test-app"

CONTINUOUS_WRITES_CTX = contextmanager(continuous_writes)


@markers.amd64_only
def test_upgrade_from_stable_amd(juju: Juju, charm: str, ubuntu_base: str):
    """Simple test to ensure that all MySQL stable revisions can be upgraded."""
    image = os.getenv("MYSQL_ROUTER_IMAGE")
    revision = os.getenv("CHARM_REVISION_AMD64")
    if revision is None:
        pytest.skip(f"No revision for {architecture.architecture} architecture")

    deploy_stable(juju, ubuntu_base, int(revision), image)

    with CONTINUOUS_WRITES_CTX(juju):
        upgrade_from_stable(juju, charm)


@markers.arm64_only
def test_upgrade_from_stable_arm(juju: Juju, charm: str, ubuntu_base: str):
    """Simple test to ensure that all MySQL stable revisions can be upgraded."""
    image = os.getenv("MYSQL_ROUTER_IMAGE")
    revision = os.getenv("CHARM_REVISION_ARM64")
    if revision is None:
        pytest.skip(f"No revision for {architecture.architecture} architecture")

    deploy_stable(juju, ubuntu_base, int(revision), image)

    with CONTINUOUS_WRITES_CTX(juju):
        upgrade_from_stable(juju, charm)


# TODO: add s390x test


def deploy_stable(juju: Juju, ubuntu_base: str, revision: int, image: str) -> None:
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
        channel="8.0/stable",
        resources={"mysql-router-image": image},
        revision=revision,
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


def upgrade_from_stable(juju: Juju, charm: str) -> None:
    """Upgrade mysqlrouter while ensuring continuous writes incrementing."""
    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    router_app_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)
    router_app_units = get_app_units(juju, MYSQL_ROUTER_APP_NAME)
    router_app_units.sort(reverse=True)

    logging.info("Refresh the charm")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=charm,
        resources={
            "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"],
        },
    )

    logging.info("Wait for refresh to start")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )

    router_status = juju.status().apps[MYSQL_ROUTER_APP_NAME]
    router_unit_status = router_status.units[router_app_units[0]].workload_status

    # Refresh will be incompatible on PR CI (not edge CI)
    # since unreleased charm versions are always marked as incompatible
    if router_unit_status.current == "blocked" and "incompatible" in router_unit_status.message:
        logging.info("Application upgrade is blocked due to incompatibility")
        juju.run(
            unit=router_app_units[0],
            action="force-refresh-start",
            params={"check-compatibility": False},
            wait=5 * MINUTE_SECS,
        )

    logging.info("Wait for first unit to upgrade")
    juju.wait(
        ready=jubilant_backports.all_agents_idle,
        timeout=5 * MINUTE_SECS,
    )

    # If leader is next to refresh, charm will be killed before action can succeed
    with suppress(jubilant_backports.TaskError):
        logging.info("Resume upgrade")
        juju.run(
            unit=router_app_leader,
            action="resume-refresh",
            wait=5 * MINUTE_SECS,
        )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)
