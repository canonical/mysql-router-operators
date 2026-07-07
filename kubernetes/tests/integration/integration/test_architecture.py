# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import jubilant
from jubilant import Juju

from .. import markers
from ..helpers_new import METADATA, MINUTE_SECS, wait_for_apps_status

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"


@markers.amd64_only
def test_arm_charm_on_amd_host(juju: Juju, charm: str) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = charm.replace("amd64", "arm64")

    resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base="ubuntu@26.04",
        resources=resources,
        num_units=1,
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_error, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )


@markers.arm64_only
def test_amd_charm_on_arm_host(juju: Juju, charm: str) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = charm.replace("arm64", "amd64")

    resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base="ubuntu@26.04",
        resources=resources,
        num_units=1,
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant.all_error, MYSQL_ROUTER_APP_NAME),
        timeout=5 * MINUTE_SECS,
    )


# TODO: add s390x test
