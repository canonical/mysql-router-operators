#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import jubilant_backports

from . import markers

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_TEST_APP_NAME = "mysql-test-app"


@markers.amd64_only
def test_arm_charm_on_amd_host(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = charm.replace("amd64", "arm64")

    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
    )
    juju.deploy(
        MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        num_units=1,
        channel="latest/edge",
        base=ubuntu_base,
    )

    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    juju.wait(
        ready=lambda status: status.apps[MYSQL_ROUTER_APP_NAME].app_status == "error",
        timeout=300,
    )


@markers.arm64_only
def test_amd_charm_on_arm_host(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = charm.replace("arm64", "amd64")

    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
    )
    juju.deploy(
        MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        num_units=1,
        channel="latest/edge",
        base=ubuntu_base,
    )

    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    juju.wait(
        ready=lambda status: status.apps[MYSQL_ROUTER_APP_NAME].app_status == "error",
        timeout=300,
    )


# TODO: add s390x test
