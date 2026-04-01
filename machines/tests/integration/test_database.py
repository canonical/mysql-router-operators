#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from time import sleep

import jubilant_backports
import pytest

from . import architecture
from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    execute_queries_against_unit,
    get_inserted_data_by_application,
    get_server_config_credentials,
    wait_for_apps_status,
)

logger = logging.getLogger(__name__)
j_logger = logging.getLogger("jubilant")
j_logger.setLevel(logging.ERROR)

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
TEST_DATABASE = "continuous_writes"
TEST_TABLE = "random_data"
SLOW_TIMEOUT = 15 * 60


@pytest.mark.abort_on_fail
def test_database_relation(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Test the database relation."""
    logger.info("Deploying MySQL, MySQL Router and application")
    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
        num_units=1,
        constraints={"arch": architecture.architecture},
    )
    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
    )
    juju.deploy(
        APPLICATION_APP_NAME,
        app=APPLICATION_APP_NAME,
        num_units=1,
        # MySQL Router is subordinate—it will use the series of the principal charm
        base=ubuntu_base,
        channel="latest/edge",
    )

    logger.info("Relating mysql, mysqlrouter and application")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:database", f"{APPLICATION_APP_NAME}:database")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")

    logger.info("Waiting for applications to be active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            APPLICATION_APP_NAME,
        ),
        timeout=SLOW_TIMEOUT,
    )

    # Ensure that the data inserted by sample application is present in the database
    application_unit = f"{APPLICATION_APP_NAME}/0"
    inserted_data = get_inserted_data_by_application(juju, application_unit)

    status = juju.status()
    mysql_unit = f"{MYSQL_APP_NAME}/0"
    mysql_unit_address = status.apps[MYSQL_APP_NAME].units[mysql_unit].public_address
    server_config_credentials = get_server_config_credentials(juju, mysql_unit)

    select_inserted_data_sql = (
        f"SELECT data FROM `{TEST_DATABASE}`.{TEST_TABLE} WHERE data = '{inserted_data}'",
    )
    selected_data = execute_queries_against_unit(
        mysql_unit_address,
        server_config_credentials["username"],
        server_config_credentials["password"],
        select_inserted_data_sql,
    )

    assert inserted_data == selected_data[0]

    # Scale and ensure that all services go to active
    # (sample application tests that it can connect to its mysqlrouter service)
    logger.info("Scaling application")
    juju.add_unit(APPLICATION_APP_NAME)

    juju.wait(
        ready=lambda status: len(status.apps[APPLICATION_APP_NAME].units) == 2,
        timeout=SLOW_TIMEOUT,
    )

    # Allow applications to change state
    sleep(30)

    logger.info("Waiting for applications to be active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_APP_NAME,
            APPLICATION_APP_NAME,
        ),
        timeout=SLOW_TIMEOUT,
    )
