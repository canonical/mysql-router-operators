# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import shutil
import zipfile
from contextlib import suppress
from pathlib import Path

import jubilant
import tomli
import tomli_w
from jubilant import Juju

from ..helpers_new import (
    METADATA,
    MINUTE_SECS,
    check_server_writes_increment,
    get_app_leader,
    get_app_units,
    wait_for_apps_status,
    wait_for_unit_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_deploy_edge(juju: Juju) -> None:
    """Simple test to ensure that mysql, mysqlrouter and application charms deploy."""
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
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=MYSQL_ROUTER_APP_NAME,
        app=MYSQL_ROUTER_APP_NAME,
        base="ubuntu@26.04",
        channel="8.4/edge",
        resources=router_resources,
        num_units=3,
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


def test_upgrade_from_edge(juju: Juju, charm: str) -> None:
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
        ready=wait_for_apps_status(jubilant.any_blocked, MYSQL_ROUTER_APP_NAME),
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
        ready=jubilant.all_agents_idle,
        timeout=5 * MINUTE_SECS,
    )

    # If leader is next to refresh, charm will be killed before action can succeed
    with suppress(jubilant.TaskError):
        logging.info("Resume upgrade")
        juju.run(
            unit=router_app_leader,
            action="resume-refresh",
            wait=5 * MINUTE_SECS,
        )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)


def test_fail_and_rollback(juju: Juju, charm: str, continuous_writes) -> None:
    """Test a refresh failure and its rollback."""
    router_app_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)
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
        resources={
            "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"],
        },
    )

    logging.info("Wait for upgrade to fail on first upgrading unit")
    juju.wait(
        ready=wait_for_unit_status(MYSQL_ROUTER_APP_NAME, router_app_units[0], "blocked"),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Ensure continuous writes are incrementing")
    check_server_writes_increment(juju, MYSQL_SERVER_APP_NAME)

    logging.info("Re-refresh the charm")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=charm,
        resources={
            "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"],
        },
    )

    logging.info("Wait for first unit to rollback")
    juju.wait(
        ready=wait_for_apps_status(jubilant.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    # If leader is next to refresh, charm will be killed before action can succeed
    with suppress(jubilant.TaskError):
        logging.info("Resume upgrade")
        juju.run(
            unit=router_app_leader,
            action="resume-refresh",
            wait=5 * MINUTE_SECS,
        )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
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

    versions["charm"] = "8.4/0.0.0"

    # Overwrite refresh_versions.toml with incompatible version.
    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        charm_zip.writestr("refresh_versions.toml", tomli_w.dumps(versions))
