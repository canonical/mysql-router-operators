#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import jubilant_backports
import pytest
import yaml

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    execute_queries_against_unit,
    get_inserted_data_by_application,
    get_server_config_credentials,
    get_unit_address,
    scale_application,
    wait_for_apps_status,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
SLOW_TIMEOUT = 15 * 60
MODEL_CONFIG = {"logging-config": "<root>=INFO;unit=DEBUG"}


@pytest.mark.abort_on_fail
def test_database_relation(juju: jubilant_backports.Juju, charm, ubuntu_base):
    """Test the database relation."""
    juju.model_config({"logging-config": MODEL_CONFIG["logging-config"]})

    mysqlrouter_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logger.info("Deploying mysql, mysqlrouter and application")
    juju.deploy(
        MYSQL_APP_NAME,
        app=MYSQL_APP_NAME,
        channel="8.0/edge",
        config={"profile": "testing"},
        base=ubuntu_base,
        num_units=3,
        trust=True,  # Necessary after a6f1f01: Fix/endpoints as k8s services (#142)
    )
    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        resources=mysqlrouter_resources,
        base=ubuntu_base,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        APPLICATION_APP_NAME,
        channel="latest/edge",
        app=APPLICATION_APP_NAME,
        base=ubuntu_base,
        num_units=1,
    )

    logger.info("Relating mysql, mysqlrouter and application")
    # Relate the database with mysqlrouter
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
    # Relate mysqlrouter with application next
    juju.integrate(f"{APPLICATION_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:database")

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

    mysql_unit = f"{MYSQL_APP_NAME}/0"
    mysql_unit_address = get_unit_address(juju, mysql_unit)
    server_config_credentials = get_server_config_credentials(juju, mysql_unit)

    select_inserted_data_sql = [
        f"SELECT data FROM continuous_writes.random_data WHERE data = '{inserted_data}'",
    ]
    selected_data = execute_queries_against_unit(
        mysql_unit_address,
        server_config_credentials["username"],
        server_config_credentials["password"],
        select_inserted_data_sql,
    )

    assert len(selected_data) > 0
    assert inserted_data == selected_data[0]

    # Ensure that both mysqlrouter and the application can be scaled up and down
    scale_application(juju, MYSQL_ROUTER_APP_NAME, 2)
    # Scaling the application will ensure that it can read the inserted data
    # from the mysqlrouter connection before going into an active status
    scale_application(juju, APPLICATION_APP_NAME, 2)

    # Disabled until juju fixes k8s scaledown: https://bugs.launchpad.net/juju/+bug/1977582
    # scale_application(juju, MYSQL_ROUTER_APP_NAME, 1)
    # scale_application(juju, APPLICATION_APP_NAME, 1)
