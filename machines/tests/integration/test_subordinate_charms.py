# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test charms subordinated alongside MySQL Router charm."""

import os

import jubilant_backports

from tests.integration.helpers import wait_for_apps_status

from .test_database import (
    APPLICATION_APP_NAME,
    MYSQL_APP_NAME,
    MYSQL_ROUTER_APP_NAME,
    SLOW_TIMEOUT,
)

UBUNTU_PRO_APP_NAME = "ubuntu-advantage"
LANDSCAPE_CLIENT_APP_NAME = "landscape-client"


def test_ubuntu_pro(juju: jubilant_backports.Juju, charm, ubuntu_base):
    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
    )
    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
    )
    juju.deploy(
        APPLICATION_APP_NAME,
        app=APPLICATION_APP_NAME,
        channel="latest/edge",
        # MySQL Router is subordinate—it will use the series of the principal charm
        base=ubuntu_base,
    )
    juju.deploy(
        UBUNTU_PRO_APP_NAME,
        app=UBUNTU_PRO_APP_NAME,
        channel="latest/edge",
        config={"token": os.environ["UBUNTU_PRO_TOKEN"]},
        base=ubuntu_base,
    )

    juju.integrate(f"{MYSQL_APP_NAME}", f"{MYSQL_ROUTER_APP_NAME}")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:database", f"{APPLICATION_APP_NAME}:database")
    juju.integrate(APPLICATION_APP_NAME, UBUNTU_PRO_APP_NAME)

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            APPLICATION_APP_NAME,
            UBUNTU_PRO_APP_NAME,
        ),
        timeout=SLOW_TIMEOUT,
    )


def test_landscape_client(juju: jubilant_backports.Juju, base):
    juju.deploy(
        LANDSCAPE_CLIENT_APP_NAME,
        app=LANDSCAPE_CLIENT_APP_NAME,
        channel="latest/edge",
        config={
            "account-name": os.environ["LANDSCAPE_ACCOUNT_NAME"],
            "registration-key": os.environ["LANDSCAPE_REGISTRATION_KEY"],
            "ppa": "ppa:landscape/self-hosted-beta",
        },
        base=base,
    )
    juju.integrate(APPLICATION_APP_NAME, LANDSCAPE_CLIENT_APP_NAME)

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            APPLICATION_APP_NAME,
            UBUNTU_PRO_APP_NAME,
        ),
        timeout=SLOW_TIMEOUT,
    )
