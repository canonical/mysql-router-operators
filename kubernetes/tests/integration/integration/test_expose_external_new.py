# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import is_connection_possible
from ..helpers_new import (
    METADATA,
    MINUTE_SECS,
    get_app_leader,
    update_interval,
    wait_for_apps_status,
)

DATA_INTEGRATOR_APP_NAME = "data-integrator"
MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"
MYSQL_TEST_APP_NAME = "mysql-test-app"

TLS_APP_NAME = "self-signed-certificates"

TEST_DATABASE_NAME = "test_database"


def test_expose_external(juju: Juju, charm: str) -> None:
    """Test the expose-external config option."""
    router_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base="ubuntu@26.04",
        channel="8.4/edge",
        config={"profile": "testing"},
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base="ubuntu@26.04",
        resources=router_resources,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=DATA_INTEGRATOR_APP_NAME,
        app=DATA_INTEGRATOR_APP_NAME,
        base="ubuntu@24.04",
        channel="latest/stable",
        config={"database-name": TEST_DATABASE_NAME},
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
        ready=wait_for_apps_status(jubilant.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    with update_interval(juju, "60s"):
        check_connectivity(juju)


def test_expose_external_with_tls(juju: Juju) -> None:
    """Test endpoints when mysql-router-k8s is related to a TLS operator."""
    logging.info("Deploying TLS application")
    juju.deploy(
        charm=TLS_APP_NAME,
        app=TLS_APP_NAME,
        base="ubuntu@24.04",
        channel="1/stable",
        config={"ca-common-name": "Test CA"},
        num_units=1,
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, TLS_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    logging.info("Relating TLS application")
    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    with update_interval(juju, "60s"):
        check_connectivity(juju)


def check_cluster_ip_endpoints(juju: Juju) -> None:
    """Helper function to check the cluster ip endpoints"""
    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)

    for attempt in Retrying(
        stop=stop_after_delay(10 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            credentials_task = juju.run(
                unit=data_integrator_leader,
                action="get-credentials",
            )

    mysql_credentials = credentials_task.results["mysql"]
    assert mysql_credentials["database"] == TEST_DATABASE_NAME
    assert mysql_credentials["username"] is not None
    assert mysql_credentials["password"] is not None

    endpoint_name = f"mysql-router-k8s-service.{juju.model}.svc.cluster.local."
    assert mysql_credentials["endpoints"] == f"{endpoint_name}:6446"
    assert mysql_credentials["read-only-endpoints"] == f"{endpoint_name}:6447"


def check_endpoint_connectivity(juju: Juju) -> None:
    """Helper function to check endpoint connectivity"""
    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)

    for attempt in Retrying(
        stop=stop_after_delay(10 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            credentials_task = juju.run(
                unit=data_integrator_leader,
                action="get-credentials",
            )

            mysql_credentials = credentials_task.results["mysql"]

            connection_config = {
                "username": mysql_credentials["username"],
                "password": mysql_credentials["password"],
                "host": mysql_credentials["endpoints"].split(",")[0].split(":")[0],
                "port": mysql_credentials["endpoints"].split(",")[0].split(":")[1],
                "ssl_disabled": False,
            }

            assert is_connection_possible(connection_config, **{"ssl_disabled": False})


def check_connectivity(juju: Juju) -> None:
    """Helper function to check connectivity"""
    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"expose-external": "false"},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Testing endpoint when expose-external=false (default)")
    check_cluster_ip_endpoints(juju)

    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"expose-external": "nodeport"},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Testing endpoint when expose-external=nodeport")
    check_endpoint_connectivity(juju)

    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"expose-external": "loadbalancer"},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
    )

    logging.info("Testing endpoint when expose-external=loadbalancer")
    check_endpoint_connectivity(juju)
