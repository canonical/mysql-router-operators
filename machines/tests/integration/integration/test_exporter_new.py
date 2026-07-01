# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import jubilant
from jubilant import Juju

from ..helpers_new import (
    MINUTE_SECS,
    check_router_metrics_endpoint,
    get_app_leader,
    wait_for_apps_status,
)

GRAFANA_AGENT_APP_NAME = "grafana-agent"
MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"


def test_exporter_endpoint(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test that exporter endpoint is functional."""
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
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        num_units=1,
    )
    juju.deploy(
        charm=GRAFANA_AGENT_APP_NAME,
        app=GRAFANA_AGENT_APP_NAME,
        base=ubuntu_base,
        channel="1/stable",
        num_units=1,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )
    juju.integrate(
        f"{MYSQL_TEST_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:database",
    )
    juju.integrate(
        f"{GRAFANA_AGENT_APP_NAME}:juju-info",
        f"{MYSQL_TEST_APP_NAME}:juju-info",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant.all_active,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_SERVER_APP_NAME,
            MYSQL_TEST_APP_NAME,
        ),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    test_app_leader = get_app_leader(juju, MYSQL_TEST_APP_NAME)
    assert not check_router_metrics_endpoint(juju, MYSQL_TEST_APP_NAME, test_app_leader)

    logging.info("Relating Grafana agent")
    juju.integrate(
        f"{GRAFANA_AGENT_APP_NAME}:cos-agent",
        f"{MYSQL_ROUTER_APP_NAME}:cos-agent",
    )

    assert check_router_metrics_endpoint(juju, MYSQL_TEST_APP_NAME, test_app_leader)

    logging.info("Unrelating Grafana agent")
    juju.remove_relation(
        f"{GRAFANA_AGENT_APP_NAME}:cos-agent",
        f"{MYSQL_ROUTER_APP_NAME}:cos-agent",
    )

    # Removing the application does not immediately make the metrics endpoint unavailable.
    # We should wait a few seconds for that to happen.
    time.sleep(30)

    assert not check_router_metrics_endpoint(juju, MYSQL_TEST_APP_NAME, test_app_leader)
