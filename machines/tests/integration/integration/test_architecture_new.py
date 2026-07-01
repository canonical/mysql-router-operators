# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import jubilant_backports
from jubilant_backports import Juju

from .. import markers
from ..helpers_new import MINUTE_SECS, wait_for_apps_status

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_TEST_APP_NAME = "mysql-test-app"


@markers.amd64_only
def test_arm_charm_on_amd_host(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = charm.replace("amd64", "arm64")

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

    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_error, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )


@markers.arm64_only
def test_amd_charm_on_arm_host(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = charm.replace("arm64", "amd64")

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

    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_error, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )


# TODO: add s390x test
