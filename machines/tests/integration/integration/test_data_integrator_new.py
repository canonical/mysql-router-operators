# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import execute_queries_against_unit
from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_unit_certificate_issuer,
    wait_for_apps_status,
)

DATA_INTEGRATOR_APP_NAME = "data-integrator"
MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"

TLS_APP_NAME = "self-signed-certificates"

TEST_DATABASE_NAME = "test_database"
TEST_TABLE_NAME = "test_table"


def test_data_integrator_connectivity(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test connectivity when backend database is using Data integrator."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
        config={"profile": "testing"},
        num_units=1,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=1,
    )
    juju.deploy(
        charm=DATA_INTEGRATOR_APP_NAME,
        app=DATA_INTEGRATOR_APP_NAME,
        base=ubuntu_base,
        channel="latest/stable",
        config={"database-name": TEST_DATABASE_NAME},
        num_units=1,
    )
    juju.deploy(
        charm=TLS_APP_NAME,
        app=TLS_APP_NAME,
        base="ubuntu@24.04",
        channel="1/stable",
        config={"ca-common-name": "Test CA"},
        num_units=1,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )
    juju.integrate(
        f"{DATA_INTEGRATOR_APP_NAME}:mysql",
        f"{MYSQL_ROUTER_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant.all_active,
            DATA_INTEGRATOR_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_SERVER_APP_NAME,
        ),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)
    data_integrator_creds = juju.run(
        unit=data_integrator_leader,
        action="get-credentials",
    )

    databases = execute_queries_against_unit(
        username=data_integrator_creds.results["mysql"]["username"],
        password=data_integrator_creds.results["mysql"]["password"],
        host=data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[0],
        port=data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[1],
        queries=["SHOW DATABASES;"],
    )

    logging.info("Ensure the database is accessible externally")
    assert TEST_DATABASE_NAME in databases


def test_data_integrator_connectivity_with_tls(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test connectivity when backend database is using TLS."""
    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)
    data_integrator_creds = juju.run(
        unit=data_integrator_leader,
        action="get-credentials",
    )

    mysql_user = data_integrator_creds.results["mysql"]["username"]
    mysql_pass = data_integrator_creds.results["mysql"]["password"]
    mysql_host = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[0]
    mysql_port = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[1]

    logging.info("Ensuring no data exists in the test database")
    tables = execute_queries_against_unit(
        username=mysql_user,
        password=mysql_pass,
        host=mysql_host,
        port=mysql_port,
        queries=[f"SHOW TABLES IN {TEST_DATABASE_NAME};"],
    )
    assert len(tables) == 0

    router_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)
    router_issuer = get_unit_certificate_issuer(juju, router_leader, mysql_host, mysql_port)
    assert "CN=MySQL_Router_Auto_Generated_CA_Certificate" in router_issuer

    logging.info("Relating TLS application")
    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN=Test CA" in (
                get_unit_certificate_issuer(juju, router_leader, "127.0.0.1", 6446)
            )

    _ = execute_queries_against_unit(
        username=mysql_user,
        password=mysql_pass,
        host=mysql_host,
        port=mysql_port,
        queries=[
            f"CREATE TABLE {TEST_DATABASE_NAME}.{TEST_TABLE_NAME} (id int, primary key(id));",
            f"INSERT INTO {TEST_DATABASE_NAME}.{TEST_TABLE_NAME} VALUES (1), (2);",
        ],
        commit=True,
    )

    data = execute_queries_against_unit(
        username=mysql_user,
        password=mysql_pass,
        host=mysql_host,
        port=mysql_port,
        queries=[f"SELECT * FROM {TEST_DATABASE_NAME}.{TEST_TABLE_NAME};"],
    )
    assert data == [1, 2]

    logging.info("Unrelating TLS application")
    juju.remove_relation(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN=MySQL_Router_Auto_Generated_CA_Certificate" in (
                get_unit_certificate_issuer(juju, router_leader, "127.0.0.1", 6446)
            )

    data = execute_queries_against_unit(
        username=mysql_user,
        password=mysql_pass,
        host=mysql_host,
        port=mysql_port,
        queries=[f"SELECT * FROM {TEST_DATABASE_NAME}.{TEST_TABLE_NAME};"],
    )
    assert data == [1, 2]
