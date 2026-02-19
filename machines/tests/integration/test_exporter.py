#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
import pytest
import requests
import tenacity

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
)

logger = logging.getLogger(__name__)

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
GRAFANA_AGENT_APP_NAME = "grafana-agent"
SLOW_TIMEOUT = 25 * 60
RETRY_TIMEOUT = 3 * 60


@pytest.mark.abort_on_fail
def test_exporter_endpoint(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Test that exporter endpoint is functional."""
    logger.info("Deploying all the applications")

    # deploy mysqlrouter with num_units=None since it's a subordinate charm
    # and will be installed with the related consumer application
    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
        num_units=1,
    )
    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        num_units=0,
        base=ubuntu_base,
    )
    juju.deploy(
        APPLICATION_APP_NAME,
        app=APPLICATION_APP_NAME,
        num_units=1,
        # MySQL Router and Grafana agent are subordinate -
        # they will use the series of the principal charm
        base=ubuntu_base,
        channel="latest/edge",
    )
    juju.deploy(
        GRAFANA_AGENT_APP_NAME,
        app=GRAFANA_AGENT_APP_NAME,
        num_units=0,
        channel="1/stable",
        base=ubuntu_base,
    )

    logger.info("Relating mysqlrouter and grafana-agent with mysql-test-app")

    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:database", f"{APPLICATION_APP_NAME}:database")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
    juju.integrate(f"{APPLICATION_APP_NAME}:juju-info", f"{GRAFANA_AGENT_APP_NAME}:juju-info")

    juju.wait(
        ready=lambda status: (
            status.apps[MYSQL_APP_NAME].app_status == "active"
            and status.apps[MYSQL_ROUTER_APP_NAME].app_status == "active"
            and status.apps[APPLICATION_APP_NAME].app_status == "active"
            and status.apps[GRAFANA_AGENT_APP_NAME].app_status == "blocked"
        ),
        timeout=SLOW_TIMEOUT,
    )

    status = juju.status()
    unit_name = f"{APPLICATION_APP_NAME}/0"
    unit_address = status.apps[APPLICATION_APP_NAME].units[unit_name].address

    try:
        requests.get(f"http://{unit_address}:9152/metrics", stream=False)
    except requests.exceptions.ConnectionError as e:
        assert "[Errno 111] Connection refused" in str(e), "❌ expected connection refused error"
    else:
        assert False, "❌ can connect to metrics endpoint without relation with cos"

    logger.info("Relating mysqlrouter with grafana agent")
    juju.integrate(f"{GRAFANA_AGENT_APP_NAME}:cos-agent", f"{MYSQL_ROUTER_APP_NAME}:cos-agent")

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
        f"{GRAFANA_AGENT_APP_NAME}:cos-agent",
        f"{MYSQL_ROUTER_APP_NAME}:cos-agent",
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
