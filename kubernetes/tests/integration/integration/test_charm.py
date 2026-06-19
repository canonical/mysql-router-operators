#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    execute_queries_against_unit,
    get_inserted_data_by_application,
    get_operator_credentials,
    get_unit_address,
    scale_application,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
SLOW_TIMEOUT = 15 * 60
MODEL_CONFIG = {"logging-config": "<root>=INFO;unit=DEBUG"}


@pytest.mark.abort_on_fail
async def test_database_relation(ops_test: OpsTest, charm):
    """Test the database relation."""
    await ops_test.model.set_config(MODEL_CONFIG)

    resource_args = [
        f"--resource=mysql-router-image={METADATA['resources']['mysql-router-image']['upstream-source']}",
    ]

    logger.info("Deploying mysql, mysqlrouter and application")
    await asyncio.gather(
        ops_test.juju(
            "deploy",
            MYSQL_APP_NAME,
            MYSQL_APP_NAME,
            "--channel=8.4/edge",
            "--config=profile=testing",
            "--base=ubuntu@24.04",
            "--num-units=3",
            "--trust",
        ),
        ops_test.juju(
            "deploy",
            charm,
            MYSQL_ROUTER_APP_NAME,
            *resource_args,
            "--base=ubuntu@24.04",
            "--num-units=1",
            "--trust",
        ),
        ops_test.juju(
            "deploy",
            APPLICATION_APP_NAME,
            APPLICATION_APP_NAME,
            "--channel=latest/edge",
            "--base=ubuntu@24.04",
            "--num-units=1",
        ),
    )

    mysql_app = ops_test.model.applications[MYSQL_APP_NAME]
    mysql_router_app = ops_test.model.applications[MYSQL_ROUTER_APP_NAME]
    application_app = ops_test.model.applications[APPLICATION_APP_NAME]

    async with ops_test.fast_forward("60s"):
        logger.info("Waiting for mysqlrouter to be in BlockedStatus")
        await asyncio.gather(
            ops_test.model.block_until(
                lambda: mysql_app.status == "active",
                timeout=SLOW_TIMEOUT,
            ),
            ops_test.model.block_until(
                lambda: mysql_router_app.status == "blocked",
                timeout=SLOW_TIMEOUT,
            ),
        )

        logger.info("Relating mysql, mysqlrouter and application")
        # Relate the database with mysqlrouter
        await ops_test.model.relate(
            f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database"
        )
        # Relate mysqlrouter with application next
        await ops_test.model.relate(
            f"{APPLICATION_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:database"
        )

        await ops_test.model.wait_for_idle(
            apps=[MYSQL_ROUTER_APP_NAME], status="active", timeout=SLOW_TIMEOUT
        )

        await ops_test.model.wait_for_idle(
            apps=[MYSQL_APP_NAME, MYSQL_ROUTER_APP_NAME, APPLICATION_APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=SLOW_TIMEOUT,
        )

    # Ensure that the data inserted by sample application is present in the database
    application_unit = application_app.units[0]
    inserted_data = await get_inserted_data_by_application(application_unit)

    mysql_unit = mysql_app.units[0]
    mysql_unit_address = await get_unit_address(ops_test, mysql_unit.name)
    operator_credentials = await get_operator_credentials(mysql_unit)

    select_inserted_data_sql = [
        f"SELECT data FROM continuous_writes.random_data WHERE data = '{inserted_data}'",
    ]
    selected_data = await execute_queries_against_unit(
        mysql_unit_address,
        operator_credentials["username"],
        operator_credentials["password"],
        select_inserted_data_sql,
    )

    assert len(selected_data) > 0
    assert inserted_data == selected_data[0]

    # Ensure that both mysqlrouter and the application can be scaled up
    await scale_application(ops_test, MYSQL_ROUTER_APP_NAME, 2)
    await scale_application(ops_test, APPLICATION_APP_NAME, 2)

    # Ensure that both mysqlrouter and the application can be scaled down
    await scale_application(ops_test, MYSQL_ROUTER_APP_NAME, 1)
    await scale_application(ops_test, APPLICATION_APP_NAME, 1)
