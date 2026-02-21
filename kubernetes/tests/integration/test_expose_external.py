#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time
from pathlib import Path

import jubilant_backports
import pytest
import tenacity
import yaml

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    get_credentials,
    is_connection_possible,
    wait_for_apps_status,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
DATA_INTEGRATOR = "data-integrator"
SLOW_TIMEOUT = 15 * 60
MODEL_CONFIG = {"logging-config": "<root>=INFO;unit=DEBUG"}
TEST_DATABASE_NAME = "testdatabase"

TLS_SETUP_SLEEP_TIME = 30
# Juju 3+ configuration for TLS
TLS_APP_NAME = "self-signed-certificates"
TLS_CHANNEL = "1/stable"
TLS_CONFIG = {"ca-common-name": "Test CA"}
TLS_BASE = "ubuntu@24.04"


def confirm_cluster_ip_endpoints(juju: jubilant_backports.Juju, model_name: str) -> None:
    """Helper function to test the cluster ip endpoints"""
    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(SLOW_TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            data_integrator_unit = f"{DATA_INTEGRATOR}/0"
            credentials = get_credentials(juju, data_integrator_unit)

    assert credentials["mysql"]["database"] == TEST_DATABASE_NAME, "Database is empty"
    assert credentials["mysql"]["username"] is not None, "Username is empty"
    assert credentials["mysql"]["password"] is not None, "Password is empty"

    endpoint_name = f"mysql-router-k8s-service.{model_name}.svc.cluster.local."
    assert credentials["mysql"]["endpoints"] == f"{endpoint_name}:6446", "Endpoint is unexpected"
    assert credentials["mysql"]["read-only-endpoints"] == f"{endpoint_name}:6447", (
        "Read-only endpoint is unexpected"
    )


def confirm_endpoint_connectivity(juju: jubilant_backports.Juju) -> None:
    """Helper to confirm endpoint connectivity"""
    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(SLOW_TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            data_integrator_unit = f"{DATA_INTEGRATOR}/0"
            credentials = get_credentials(juju, data_integrator_unit)
            assert credentials["mysql"]["endpoints"] is not None, "Endpoints missing"

            connection_config = {
                "username": credentials["mysql"]["username"],
                "password": credentials["mysql"]["password"],
                "host": credentials["mysql"]["endpoints"].split(",")[0].split(":")[0],
            }

            extra_connection_options = {
                "port": credentials["mysql"]["endpoints"].split(":")[1],
                "ssl_disabled": False,
            }

            assert is_connection_possible(connection_config, **extra_connection_options), (
                "Connection not possible through endpoints"
            )


@pytest.mark.abort_on_fail
def test_expose_external(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Test the expose-external config option."""
    juju.model_config({"logging-config": MODEL_CONFIG["logging-config"]})

    mysql_router_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logger.info("Deploying mysql-k8s, mysql-router-k8s and data-integrator")
    juju.deploy(
        MYSQL_APP_NAME,
        app=MYSQL_APP_NAME,
        channel="8.0/edge",
        config={"profile": "testing"},
        base=ubuntu_base,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        resources=mysql_router_resources,
        base=ubuntu_base,
        num_units=1,
        trust=True,
    )
    juju.deploy(
        DATA_INTEGRATOR,
        app=DATA_INTEGRATOR,
        channel="latest/edge",
        config={"database-name": TEST_DATABASE_NAME},
        base="ubuntu@24.04",
        num_units=1,
    )

    logger.info("Relating mysql-k8s, mysql-router-k8s and data-integrator")
    juju.integrate(f"{MYSQL_APP_NAME}:database", f"{MYSQL_ROUTER_APP_NAME}:backend-database")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:database", f"{DATA_INTEGRATOR}:mysql")

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            DATA_INTEGRATOR,
        ),
        timeout=SLOW_TIMEOUT,
    )

    logger.info("Testing endpoint when expose-external=false (default)")
    confirm_cluster_ip_endpoints(juju, juju.model)

    logger.info("Testing endpoint when expose-external=nodeport")
    juju.config(MYSQL_ROUTER_APP_NAME, {"expose-external": "nodeport"})
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=SLOW_TIMEOUT,
    )

    confirm_endpoint_connectivity(juju)

    logger.info("Testing endpoint when expose-external=loadbalancer")
    juju.config(MYSQL_ROUTER_APP_NAME, {"expose-external": "loadbalancer"})
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=SLOW_TIMEOUT,
    )

    confirm_endpoint_connectivity(juju)


@pytest.mark.abort_on_fail
def test_expose_external_with_tls(juju: jubilant_backports.Juju) -> None:
    """Test endpoints when mysql-router-k8s is related to a TLS operator."""
    logger.info("Resetting expose-external=false")
    juju.config(MYSQL_ROUTER_APP_NAME, {"expose-external": "false"})
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=SLOW_TIMEOUT,
    )

    logger.info("Deploying TLS operator")
    juju.deploy(
        TLS_APP_NAME,
        channel=TLS_CHANNEL,
        config=TLS_CONFIG,
        base=TLS_BASE,
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, TLS_APP_NAME),
        timeout=SLOW_TIMEOUT,
    )

    logger.info("Relate mysql-router-k8s with TLS operator")
    juju.integrate(MYSQL_ROUTER_APP_NAME, TLS_APP_NAME)

    time.sleep(TLS_SETUP_SLEEP_TIME)

    logger.info("Testing endpoint when expose-external=false(default)")
    confirm_cluster_ip_endpoints(juju, juju.model)

    logger.info("Testing endpoint when expose-external=nodeport")
    juju.config(MYSQL_ROUTER_APP_NAME, {"expose-external": "nodeport"})
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=SLOW_TIMEOUT,
    )

    confirm_endpoint_connectivity(juju)

    logger.info("Testing endpoint when expose-external=loadbalancer")
    juju.config(MYSQL_ROUTER_APP_NAME, {"expose-external": "loadbalancer"})
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=SLOW_TIMEOUT,
    )

    confirm_endpoint_connectivity(juju)
