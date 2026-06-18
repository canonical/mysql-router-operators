# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test charms subordinated alongside MySQL Router charm."""

import asyncio
import os

from .test_database import (
    APPLICATION_APP_NAME,
    MYSQL_APP_NAME,
    MYSQL_ROUTER_APP_NAME,
    SLOW_TIMEOUT,
)

UBUNTU_PRO_APP_NAME = "ubuntu-advantage"
LANDSCAPE_CLIENT_APP_NAME = "landscape-client"


async def test_ubuntu_pro(ops_test, charm, ubuntu_base):
    await asyncio.gather(
        ops_test.juju(
            "deploy",
            MYSQL_APP_NAME,
            MYSQL_APP_NAME,
            "--channel=8.4/edge",
            "--config=profile=testing",
        ),
        ops_test.juju(
            "deploy",
            charm,
            MYSQL_ROUTER_APP_NAME,
            f"--base={ubuntu_base}",
        ),
        ops_test.juju(
            "deploy",
            APPLICATION_APP_NAME,
            APPLICATION_APP_NAME,
            "--channel=latest/edge",
            f"--base={ubuntu_base}",
        ),
        ops_test.juju(
            "deploy",
            UBUNTU_PRO_APP_NAME,
            UBUNTU_PRO_APP_NAME,
            "--channel=latest/edge",
            f"--config=token={os.environ['UBUNTU_PRO_TOKEN']}",
            f"--base={ubuntu_base}",
        ),
    )
    await ops_test.model.relate(
        f"{MYSQL_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:backend-database"
    )
    await ops_test.model.relate(
        f"{APPLICATION_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:database"
    )
    await ops_test.model.relate(
        f"{APPLICATION_APP_NAME}:juju-info", f"{UBUNTU_PRO_APP_NAME}:juju-info"
    )
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[
                MYSQL_APP_NAME,
                MYSQL_ROUTER_APP_NAME,
                APPLICATION_APP_NAME,
                UBUNTU_PRO_APP_NAME,
            ],
            status="active",
            timeout=SLOW_TIMEOUT,
        )


async def test_landscape_client(ops_test, ubuntu_base):
    await ops_test.juju(
        "deploy",
        LANDSCAPE_CLIENT_APP_NAME,
        LANDSCAPE_CLIENT_APP_NAME,
        "--channel=latest/edge",
        "--config=ppa=ppa:landscape/self-hosted-beta",
        f"--config=account-name={os.environ['LANDSCAPE_ACCOUNT_NAME']}",
        f"--config=registration-key={os.environ['LANDSCAPE_REGISTRATION_KEY']}",
        f"--base={ubuntu_base}",
    )
    await ops_test.model.relate(APPLICATION_APP_NAME, LANDSCAPE_CLIENT_APP_NAME)
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[
                MYSQL_APP_NAME,
                MYSQL_ROUTER_APP_NAME,
                APPLICATION_APP_NAME,
                LANDSCAPE_CLIENT_APP_NAME,
            ],
            status="active",
            raise_on_blocked=True,
            timeout=SLOW_TIMEOUT,
        )
