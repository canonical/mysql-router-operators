# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
from jubilant_backports import Juju

from ..helpers import execute_queries_against_unit
from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_mysql_server_credentials,
    get_unit_address,
    wait_for_apps_status,
)

KEYSTONE_APP_NAME = "keystone"
MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"


def test_shared_db(juju: Juju, charm: str, ubuntu_base: str):
    """Test the shared-db legacy relation."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/edge",
        config={"profile": "testing"},
        num_units=1,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=0,
    )
    juju.deploy(
        charm=KEYSTONE_APP_NAME,
        app=KEYSTONE_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        num_units=2,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )
    juju.integrate(
        f"{KEYSTONE_APP_NAME}:shared-db",
        f"{MYSQL_ROUTER_APP_NAME}:shared-db",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    mysql_leader = get_app_leader(juju, MYSQL_SERVER_APP_NAME)
    mysql_creds = get_mysql_server_credentials(juju, mysql_leader, "serverconfig")

    tables = execute_queries_against_unit(
        username=mysql_creds["username"],
        password=mysql_creds["password"],
        host=get_unit_address(juju, MYSQL_SERVER_APP_NAME, mysql_leader),
        port=3306,
        queries=["SHOW TABLES IN 'keystone';"],
    )
    assert len(tables) > 0
