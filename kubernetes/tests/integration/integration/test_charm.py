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
    get_server_config_credentials,
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
async def test_database_relation(ops_test: OpsTest, charm, series):
    """Test the database relation."""
    await ops_test.model.set_config(MODEL_CONFIG)

    mysqlrouter_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logger.info("Deploying mysql, mysqlrouter and application")
    applications = await asyncio.gather(
        ops_test.model.deploy(
            MYSQL_APP_NAME,
            channel="8.0/edge",
            application_name=MYSQL_APP_NAME,
            config={"profile": "testing"},
            series=series,
            num_units=3,
            trust=True,  # Necessary after a6f1f01: Fix/endpoints as k8s services (#142)
        ),
        ops_test.model.deploy(
            charm,
            application_name=MYSQL_ROUTER_APP_NAME,
            resources=mysqlrouter_resources,
            series=series,
            num_units=1,
            trust=True,
        ),
        ops_test.model.deploy(
            APPLICATION_APP_NAME,
            channel="latest/edge",
            application_name=APPLICATION_APP_NAME,
            series=series,
            num_units=1,
        ),
    )

    mysql_app, application_app = applications[0], applications[2]
    logger.info("Relating mysql, mysqlrouter and application")
    # Relate the database with mysqlrouter
    await ops_test.model.relate(
        f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database"
    )
    # Relate mysqlrouter with application next
    await ops_test.model.relate(
        f"{APPLICATION_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:database"
    )

    async with ops_test.fast_forward():
        logger.info("Waiting for test-app to be in active")

        # mysql-test-app only becomes active when connection to DB is successful
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_APP_NAME], status="active", timeout=SLOW_TIMEOUT
        )

    # Ensure that the data inserted by sample application is present in the database
    application_unit = application_app.units[0]
    inserted_data = await get_inserted_data_by_application(application_unit)

    mysql_unit = mysql_app.units[0]
    mysql_unit_address = await get_unit_address(ops_test, mysql_unit.name)
    server_config_credentials = await get_server_config_credentials(mysql_unit)

    select_inserted_data_sql = [
        f"SELECT data FROM continuous_writes.random_data WHERE data = '{inserted_data}'",
    ]
    selected_data = await execute_queries_against_unit(
        mysql_unit_address,
        server_config_credentials["username"],
        server_config_credentials["password"],
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
