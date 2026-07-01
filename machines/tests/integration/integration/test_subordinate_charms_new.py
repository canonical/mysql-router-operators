# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os

import jubilant
from jubilant import Juju

from ..helpers_new import (
    MINUTE_SECS,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

LANDSCAPE_APP_NAME = "landscape-client"
UBUNTU_PRO_APP_NAME = "ubuntu-advantage"


def test_ubuntu_pro(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Tests Ubuntu pro charm alongside the MySQL charms."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
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
    juju.deploy(
        charm=UBUNTU_PRO_APP_NAME,
        app=UBUNTU_PRO_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        config={"token": os.environ["UBUNTU_PRO_TOKEN"]},
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
    juju.integrate(
        f"{MYSQL_TEST_APP_NAME}:juju-info",
        f"{UBUNTU_PRO_APP_NAME}:juju-info",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )


def test_landscape_client(juju: Juju, ubuntu_base: str):
    """Tests Landscape client charm alongside the MySQL charms."""
    juju.deploy(
        charm=LANDSCAPE_APP_NAME,
        app=LANDSCAPE_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        config={
            "account-name": os.environ["LANDSCAPE_ACCOUNT_NAME"],
            "registration-key": os.environ["LANDSCAPE_REGISTRATION_KEY"],
            "ppa": "ppa:landscape/self-hosted-beta",
        },
        num_units=1,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{LANDSCAPE_APP_NAME}:juju-info",
        f"{MYSQL_TEST_APP_NAME}:juju-info",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )
