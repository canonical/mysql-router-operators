# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from jubilant_backports import Juju

from .. import markers
from ..helpers_new import METADATA, MINUTE_SECS, wait_for_unit_status

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"


@markers.amd64_only
def test_arm_charm_on_amd_host(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = charm.replace("amd64", "arm64")

    resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        resources=resources,
        num_units=1,
    )

    # We must check the unit status, instead of the application status,
    # because Juju 2.9 leaves the application in "waiting" status until
    # the units are bootstrapped. This never happens as the units error.
    juju.wait(
        ready=wait_for_unit_status(MYSQL_ROUTER_APP_NAME, f"{MYSQL_ROUTER_APP_NAME}/0", "error"),
        timeout=5 * MINUTE_SECS,
    )


@markers.arm64_only
def test_amd_charm_on_arm_host(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = charm.replace("arm64", "amd64")

    resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        resources=resources,
        num_units=1,
    )

    # We must check the unit status, instead of the application status,
    # because Juju 2.9 leaves the application in "waiting" status until
    # the units are bootstrapped. This never happens as the units error.
    juju.wait(
        ready=wait_for_unit_status(MYSQL_ROUTER_APP_NAME, f"{MYSQL_ROUTER_APP_NAME}/0", "error"),
        timeout=5 * MINUTE_SECS,
    )


# TODO: add s390x test
