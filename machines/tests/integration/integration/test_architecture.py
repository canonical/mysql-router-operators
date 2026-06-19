#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio

from pytest_operator.plugin import OpsTest

from .. import markers

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_TEST_APP_NAME = "mysql-test-app"


@markers.amd64_only
async def test_arm_charm_on_amd_host(ops_test: OpsTest, charm, ubuntu_base) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = charm.replace("amd64", "arm64")

    await asyncio.gather(
        ops_test.juju(
            "deploy",
            charm,
            MYSQL_ROUTER_APP_NAME,
            f"--base={ubuntu_base}",
        ),
        ops_test.juju(
            "deploy",
            MYSQL_TEST_APP_NAME,
            MYSQL_TEST_APP_NAME,
            "--channel=latest/edge",
            f"--base={ubuntu_base}",
            "--num-units=1",
        ),
    )

    await ops_test.model.relate(
        f"{MYSQL_ROUTER_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    await ops_test.model.wait_for_idle(
        apps=[MYSQL_ROUTER_APP_NAME],
        status="error",
        raise_on_error=False,
    )


@markers.arm64_only
async def test_amd_charm_on_arm_host(ops_test: OpsTest, charm, ubuntu_base) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = charm.replace("arm64", "amd64")

    await asyncio.gather(
        ops_test.juju(
            "deploy",
            charm,
            MYSQL_ROUTER_APP_NAME,
            f"--base={ubuntu_base}",
        ),
        ops_test.juju(
            "deploy",
            MYSQL_TEST_APP_NAME,
            MYSQL_TEST_APP_NAME,
            "--channel=latest/edge",
            f"--base={ubuntu_base}",
            "--num-units=1",
        ),
    )

    await ops_test.model.relate(
        f"{MYSQL_ROUTER_APP_NAME}:database",
        f"{MYSQL_TEST_APP_NAME}:database",
    )

    await ops_test.model.wait_for_idle(
        apps=[MYSQL_ROUTER_APP_NAME],
        status="error",
        raise_on_error=False,
    )


# TODO: add s390x test
