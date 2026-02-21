# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import pathlib
import shutil
import time
import zipfile
from pathlib import Path

import jubilant_backports
import pytest
import tomli
import tomli_w
import yaml

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    ensure_all_units_continuous_writes_incrementing,
    get_leader_unit,
    wait_for_apps_status,
)
from .juju_ import run_action

logger = logging.getLogger(__name__)
j_logger = logging.getLogger("jubilant")
j_logger.setLevel(logging.ERROR)

TIMEOUT = 20 * 60
UPGRADE_TIMEOUT = 15 * 60
SMALL_TIMEOUT = 5 * 60

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
RESOURCES = {"mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]}


@pytest.mark.abort_on_fail
def test_deploy_edge(juju: jubilant_backports.Juju, ubuntu_base) -> None:
    """Simple test to ensure that mysql, mysqlrouter and application charms deploy."""
    logger.info("Deploying all applications")

    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
        base=ubuntu_base,
        num_units=1,
        trust=True,  # Necessary after a6f1f01: Fix/endpoints as k8s services (#142)
    )
    juju.deploy(
        MYSQL_ROUTER_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=3,
        trust=True,  # Necessary after a6f1f01: Fix/endpoints as k8s services (#142)
    )
    juju.deploy(
        APPLICATION_APP_NAME,
        channel="latest/edge",
        app=APPLICATION_APP_NAME,
        base=ubuntu_base,
        num_units=1,
        config={"sleep_interval": "500"},
    )

    logger.info(f"Relating {MYSQL_ROUTER_APP_NAME} to {MYSQL_APP_NAME} and {APPLICATION_APP_NAME}")

    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
    juju.integrate(f"{APPLICATION_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:database")

    logger.info("Waiting for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            APPLICATION_APP_NAME,
        ),
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
def test_upgrade_from_edge(juju: jubilant_backports.Juju, charm) -> None:
    """Upgrade mysqlrouter while ensuring continuous writes incrementing."""
    ensure_all_units_continuous_writes_incrementing(juju)

    logger.info("Refresh the charm")
    juju.refresh(
        MYSQL_ROUTER_APP_NAME,
        path=charm,
        resources={"mysql-router-image": RESOURCES["mysql-router-image"]},
    )

    # Get unit list sorted by highest to lowest unit number
    first_unit = f"{MYSQL_ROUTER_APP_NAME}/2"

    logger.info("Wait for refresh to start")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=5 * 60,
    )
    app_status_message = juju.status().apps[MYSQL_ROUTER_APP_NAME].app_status.message
    assert "resume-refresh" in app_status_message, (
        "mysql router application status not indicating that user should resume refresh"
    )

    logger.info("Wait for first unit to restart")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_agents_idle, MYSQL_ROUTER_APP_NAME),
        timeout=5 * 60,
    )

    # Refresh will be incompatible on PR CI (not edge CI) since unreleased charm versions are
    # always marked as incompatible
    first_unit_status = juju.status().apps[MYSQL_ROUTER_APP_NAME].units[first_unit].workload_status
    if first_unit_status.current == "blocked" and "incompatible" in first_unit_status.message:
        logger.info("Running force-refresh-start action with check-compatibility=false")
        run_action(juju, first_unit, "force-refresh-start", **{"check-compatibility": False})

    logger.info("Wait for first unit to upgrade")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_agents_idle, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )

    mysql_router_leader_unit = get_leader_unit(juju, MYSQL_ROUTER_APP_NAME)
    assert mysql_router_leader_unit, "Can't find router leader"
    logger.info("Running resume-refresh on the mysql router leader unit")
    # If leader is next to refresh, charm will be killed before action can succeed
    # so we don't check return code
    try:
        run_action(juju, mysql_router_leader_unit, "resume-refresh")
    except Exception:
        pass  # Expected if leader is being refreshed

    logger.info("Waiting for upgrade to complete on all units")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=UPGRADE_TIMEOUT,
    )

    ensure_all_units_continuous_writes_incrementing(juju)

    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
def test_fail_and_rollback(juju: jubilant_backports.Juju, charm, continuous_writes) -> None:
    """Upgrade to an invalid version and test rollback.

    Relies on the charm built in the previous test (test_upgrade_from_edge).
    """
    ensure_all_units_continuous_writes_incrementing(juju)

    fault_charm = "./faulty.charm"
    shutil.copy(charm, fault_charm)

    logger.info("Creating invalid upgrade charm")
    create_invalid_upgrade_charm(fault_charm)

    logger.info("Refreshing mysql router with an invalid charm")
    juju.refresh(
        MYSQL_ROUTER_APP_NAME,
        path=fault_charm,
        resources={"mysql-router-image": RESOURCES["mysql-router-image"]},
    )

    logger.info("Wait for refresh to block as incompatible")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )
    first_unit = f"{MYSQL_ROUTER_APP_NAME}/2"
    first_unit_status = juju.status().apps[MYSQL_ROUTER_APP_NAME].units[first_unit].workload_status
    assert "incompatible" in first_unit_status.message, (
        "mysql router application status not indicating that refresh incompatible"
    )

    logger.info("Ensure continuous writes while in failure state")
    ensure_all_units_continuous_writes_incrementing(juju)

    logger.info("Re-refresh the charm")
    juju.refresh(
        MYSQL_ROUTER_APP_NAME,
        path=charm,
        resources={"mysql-router-image": RESOURCES["mysql-router-image"]},
    )

    # sleep to ensure that active status from before re-refresh does not affect below check
    time.sleep(15)

    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_agents_idle, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )

    logger.info("Wait for blocked app status")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=5 * 60,
    )
    app_status_message = juju.status().apps[MYSQL_ROUTER_APP_NAME].app_status.message
    assert "resume-refresh" in app_status_message, (
        "mysql router application status not indicating that user should resume refresh"
    )

    logger.info("Wait for first unit to rollback")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_agents_idle, MYSQL_ROUTER_APP_NAME),
        timeout=TIMEOUT,
    )

    mysql_router_leader_unit = get_leader_unit(juju, MYSQL_ROUTER_APP_NAME)
    assert mysql_router_leader_unit, "Can't find router leader"
    logger.info("Running resume-refresh on the mysql router leader unit")
    # If leader is next to refresh, charm will be killed before action can succeed
    try:
        run_action(juju, mysql_router_leader_unit, "resume-refresh")
    except Exception:
        pass  # Expected if leader is being refreshed

    logger.info("Waiting for rollback to complete on all units")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=UPGRADE_TIMEOUT,
    )

    logger.info("Ensure continuous writes after rollback procedure")
    ensure_all_units_continuous_writes_incrementing(juju)

    os.remove(fault_charm)


def create_invalid_upgrade_charm(charm_file: str | pathlib.Path) -> None:
    """Create an invalid mysql router charm for upgrade."""
    with zipfile.ZipFile(charm_file, mode="r") as charm_zip:
        with zipfile.Path(charm_zip, "refresh_versions.toml").open("rb") as file:
            versions = tomli.load(file)

    # "charm" is added during pack time using the charm refresh compatibility version stored as a git tag
    # so charm can be set after the release (when the version is determined) but before pack time
    versions["charm"] = "8.0/0.0.0"

    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        # an invalid charm version because the major workload_version is one less than the current workload_version
        charm_zip.writestr("refresh_versions.toml", tomli_w.dumps(versions))
