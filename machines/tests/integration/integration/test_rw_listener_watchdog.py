# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
from jubilant_backports import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_rw_listener_watchdog(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test the RW listener watchdog process, REST user and healthy-dispatch no-op."""
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
        ready=wait_for_apps_status(jubilant_backports.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    router_unit = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)
    agent_dir = f"/var/lib/juju/agents/unit-{router_unit.replace('/', '-')}"
    snap_service = "snap.charmed-mysql.mysqlrouter-service"

    logging.info("Checking the watchdog process is running")
    # `[w]` stops pgrep from matching the shell running this very command line
    juju.exec("pgrep -f '[w]atchdog/main.py'", unit=router_unit)

    logging.info("Checking the watchdog state files are in the unit agent directory")
    for file_name in ("rw-listener-watchdog.pid", "rw-listener-watchdog.cred"):
        juju.exec(f"test -f {agent_dir}/{file_name}", unit=router_unit)

    logging.info("Checking the monitoring REST user exists (no COS related)")
    users = juju.exec(
        "charmed-mysql.mysqlrouter-passwd list "
        "/var/snap/charmed-mysql/current/etc/mysqlrouter/rest_api_credentials",
        unit=router_unit,
    ).stdout
    assert "monitoring" in users

    logging.info("Recording MySQL Router service main PID")
    pid_before = juju.exec(
        f"systemctl show -p MainPID --value {snap_service}", unit=router_unit
    ).stdout.strip()
    assert pid_before not in ("", "0")

    logging.info("Dispatching rw_listener_stuck on a healthy router")
    juju.exec(
        "JUJU_DISPATCH_PATH=hooks/rw_listener_stuck ./dispatch",
        unit=router_unit,
    )

    logging.info("Checking the event was a no-op")
    # Log shipping to the controller is asynchronous: poll instead of reading once
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
        with attempt:
            assert "no route is stuck anymore" in juju.debug_log()
    pid_after = juju.exec(
        f"systemctl show -p MainPID --value {snap_service}", unit=router_unit
    ).stdout.strip()
    assert pid_after == pid_before
