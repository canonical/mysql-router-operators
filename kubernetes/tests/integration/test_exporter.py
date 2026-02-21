#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant_backports
import pytest
import requests
import tenacity
import yaml

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    get_unit_address,
    wait_for_apps_status,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
GRAFANA_AGENT_APP_NAME = "grafana-agent-k8s"
SLOW_TIMEOUT = 25 * 60
RETRY_TIMEOUT = 3 * 60


@pytest.mark.abort_on_fail
def test_exporter_endpoint(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Test that exporter endpoint is functional."""
    mysqlrouter_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logger.info("Deploying all the applications")

    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
        base=ubuntu_base,
        num_units=1,
        trust=True,
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
    juju.deploy(
        GRAFANA_AGENT_APP_NAME,
        channel="1/stable",
        app=GRAFANA_AGENT_APP_NAME,
        base=ubuntu_base,
        num_units=1,
    )

    logger.info("Relating mysql, mysqlrouter and application")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
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

    unit_name = f"{MYSQL_ROUTER_APP_NAME}/0"
    unit_address = get_unit_address(juju, unit_name)

    try:
        requests.get(f"http://{unit_address}:9152/metrics", stream=False)
    except requests.exceptions.ConnectionError as e:
        assert "[Errno 111] Connection refused" in str(e), "❌ expected connection refused error"
    else:
        assert False, "❌ can connect to metrics endpoint without relation with cos"

    logger.info("Relating mysqlrouter with grafana agent")
    juju.integrate(
        f"{GRAFANA_AGENT_APP_NAME}:grafana-dashboards-consumer",
        f"{MYSQL_ROUTER_APP_NAME}:grafana-dashboard",
    )
    juju.integrate(
        f"{GRAFANA_AGENT_APP_NAME}:logging-provider", f"{MYSQL_ROUTER_APP_NAME}:logging"
    )

    juju.integrate(
        f"{GRAFANA_AGENT_APP_NAME}:metrics-endpoint", f"{MYSQL_ROUTER_APP_NAME}:metrics-endpoint"
    )

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(RETRY_TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            response = requests.get(f"http://{unit_address}:9152/metrics", stream=False)
            response.raise_for_status()
            assert "mysqlrouter_route_health" in response.text, (
                "❌ did not find expected metric in response"
            )
            response.close()

    logger.info("Removing relation between mysqlrouter and grafana agent")
    juju.remove_relation(
        f"{GRAFANA_AGENT_APP_NAME}:metrics-endpoint",
        f"{MYSQL_ROUTER_APP_NAME}:metrics-endpoint",
    )

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(RETRY_TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            try:
                requests.get(f"http://{unit_address}:9152/metrics", stream=False)
            except requests.exceptions.ConnectionError as e:
                assert "[Errno 111] Connection refused" in str(e), (
                    "❌ expected connection refused error"
                )
            else:
                assert False, "❌ can connect to metrics endpoint without relation with cos"
