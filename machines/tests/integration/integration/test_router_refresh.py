# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import shutil
import zipfile
from pathlib import Path

import jubilant
import tomli
import tomli_w
from jubilant import Juju

from ..helpers_new import MINUTE_SECS, wait_for_apps_status

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_router_snap_refresh(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Deploy mysql-router from a local charm with the snap fix, then trigger a snap refresh.

    This test demonstrates that the snap-side fix (moving mysqlrouter data to $SNAP_COMMON)
    eliminates the ``snap start`` failure after a refresh, without needing the charm-side
    resiliency improvements (retry, port check, WaitingStatus).

    The local charm must be built with a ``refresh_versions.toml`` that pins the fixed
    snap revision (e.g. rev 245, channel ``8.4/edge/fix-mysqlrouter-data``).
    """
    logging.info("Deploying mysql from 8.4/edge")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
        config={"profile": "testing"},
        num_units=1,
    )

    logging.info("Deploying mysql-router from local charm (with snap fix)")
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=1,
    )

    logging.info("Deploying mysql-test-app")
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

    # Build a charm that targets a different fixed snap revision to trigger
    # a real snap refresh (rev 245/247 -> 246/248). Both revisions have the
    # $SNAP_COMMON layout fix, so the config paths survive the refresh.
    # Without the snap fix, the service would crash after the refresh because
    # the old config references the old revision's $SNAP_DATA directory,
    # which is blocked by AppArmor.
    tmp_folder = Path("tmp")
    tmp_folder.mkdir(exist_ok=True)
    upgrade_charm = Path(tmp_folder, charm).absolute()
    shutil.copy(charm, upgrade_charm)
    _set_snap_revision(upgrade_charm, x86_64="246", aarch64="248", workload="8.4.10")

    logging.info("Refreshing charm to trigger snap refresh (rev 245/247 -> 246/248, both fixed)")
    juju.refresh(
        app=MYSQL_ROUTER_APP_NAME,
        path=upgrade_charm,
    )

    logging.info("Wait for refresh to start")
    juju.wait(
        ready=wait_for_apps_status(jubilant.any_blocked, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )

    model_status = juju.status()
    router_status = model_status.apps[MYSQL_ROUTER_APP_NAME].app_status

    # Refresh will be incompatible on PR CI (not edge CI)
    # since unreleased charm versions are always marked as incompatible
    if router_status.current == "blocked" and "incompatible" in router_status.message:
        logging.info("Application upgrade is blocked due to incompatibility")
        juju.run(
            unit=f"{MYSQL_ROUTER_APP_NAME}/0",
            action="force-refresh-start",
            params={"check-compatibility": False},
            wait=5 * MINUTE_SECS,
        )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Verifying router is listening on port 6446 after snap refresh")
    units = juju.status().get_units(MYSQL_ROUTER_APP_NAME)
    for unit_name in units:
        unit = units[unit_name]
        assert "6446" in " ".join(str(p) for p in unit.open_ports), (
            f"Unit {unit_name} does not expose port 6446"
        )
    logging.info(
        "Router is listening on port 6446 — snap fix works without resiliency improvements"
    )


def _set_snap_revision(
    charm_file: str | Path,
    *,
    x86_64: str,
    aarch64: str,
    workload: str,
) -> None:
    """Set the snap revision and workload version in a charm's refresh_versions.toml."""
    with zipfile.ZipFile(charm_file, mode="r") as charm_zip:
        with zipfile.Path(charm_zip, "refresh_versions.toml").open("rb") as file:
            versions = tomli.load(file)

    versions["snap"]["revisions"]["x86_64"] = x86_64
    versions["snap"]["revisions"]["aarch64"] = aarch64
    versions["workload"] = workload

    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        charm_zip.writestr("refresh_versions.toml", tomli_w.dumps(versions))
