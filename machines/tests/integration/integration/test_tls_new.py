# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_unit_certificate_issuer,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

TLS_APP_NAME = "self-signed-certificates"

MYSQL_ROUTER_SOCKET = "/var/snap/charmed-mysql/common/run/mysqlrouter/mysql.sock"


def test_deploy_and_relate(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test encryption when backend database is using TLS."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
        config={"profile": "testing"},
        # MySQL Router 8.4 requires cluster quorum for R/W traffic,
        # because of the unreachable_quorum_allowed_traffic config option
        # (only observable upon process restart)
        num_units=3,
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
    juju.deploy(
        charm=TLS_APP_NAME,
        app=TLS_APP_NAME,
        base="ubuntu@24.04",
        channel="1/stable",
        config={"ca-common-name": "Test CA"},
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


def test_connected_encryption(juju: Juju) -> None:
    """Test encryption when backend database is using TLS."""
    router_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN=MySQL_Router_Auto_Generated_CA_Certificate" in (
                get_unit_certificate_issuer(juju, router_leader, MYSQL_ROUTER_SOCKET)
            )

    logging.info("Relating TLS application")
    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN=Test CA" in (
                get_unit_certificate_issuer(juju, router_leader, MYSQL_ROUTER_SOCKET)
            )

    logging.info("Unrelating TLS application")
    juju.remove_relation(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN=MySQL_Router_Auto_Generated_CA_Certificate" in (
                get_unit_certificate_issuer(juju, router_leader, MYSQL_ROUTER_SOCKET)
            )
