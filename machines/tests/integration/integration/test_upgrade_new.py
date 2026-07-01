# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import shutil
import zipfile
from contextlib import suppress
from pathlib import Path

import jubilant_backports
import tomli
import tomli_w
from jubilant_backports import Juju

from ..helpers_new import (
    MINUTE_SECS,
    check_server_writes_increment,
    get_app_units,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_deploy_edge(juju: Juju, ubuntu_base: str) -> None:
    """Simple test to ensure that mysql, mysqlrouter and application charms deploy."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/edge",
        config={"profile": "testing"},
        num_units=1,
    )
    juju.deploy(
        charm=MYSQL_ROUTER_APP_NAME,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        channel="dpe/edge",
        num_units=1,
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


def test_upgrade_from_edge(juju: Juju, charm: str, continuous_writes) -> None:
    """Upgrade mysqlrouter while ensuring continuous writes incrementing."""
    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    router_app_units = get_app_units(juju, MYSQL_ROUTER_APP_NAME)
    router_app_units.sort(reverse=True)

    logging.info("Refresh the charm")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=charm,
    )

    logging.info("Wait for refresh to start")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )

    model_status = juju.status()
    router_status = model_status.apps[MYSQL_ROUTER_APP_NAME].app_status

    # Refresh will be incompatible on PR CI (not edge CI)
    # since unreleased charm versions are always marked as incompatible
    if router_status.current == "blocked" and "incompatible" in router_status.message:
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
            unit=router_app_units[1],
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


def test_fail_and_rollback(juju: Juju, charm: str, continuous_writes) -> None:
    """Test a refresh failure and its rollback."""
    router_app_units = get_app_units(juju, MYSQL_ROUTER_APP_NAME)
    router_app_units.sort(reverse=True)

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    tmp_folder = Path("tmp")
    tmp_folder.mkdir(exist_ok=True)
    tmp_folder_charm = Path(tmp_folder, charm).absolute()

    shutil.copy(charm, tmp_folder_charm)

    logging.info("Inject dependency fault")
    inject_dependency_fault(tmp_folder_charm)

    logging.info("Refresh the charm")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=tmp_folder_charm,
    )

    logging.info("Wait for upgrade to fail on first upgrading unit")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    logging.info("Re-refresh the charm")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=charm,
    )

    logging.info("Wait for rollback to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes after rollback procedure")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    # Remove fault charm file
    tmp_folder_charm.unlink()


def inject_dependency_fault(charm_file: str | Path) -> None:
    """Inject a dependency fault into the MySQL charm."""
    with Path("refresh_versions.toml").open("rb") as file:
        versions = tomli.load(file)

    versions["charm"] = "8.0/0.0.0"

    # Overwrite refresh_versions.toml with incompatible version.
    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        charm_zip.writestr("refresh_versions.toml", tomli_w.dumps(versions))
