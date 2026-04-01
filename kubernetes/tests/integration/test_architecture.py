#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import jubilant_backports
import yaml

from . import markers

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
MYSQL_ROUTER_APP_NAME = METADATA["name"]


@markers.amd64_only
def test_arm_charm_on_amd_host(juju: jubilant_backports.Juju, ubuntu_base) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = "./mysql-router-k8s_ubuntu@22.04-arm64.charm"

    resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        num_units=1,
        resources=resources,
        base=ubuntu_base,
    )

    juju.wait(
        ready=lambda status: status.apps[MYSQL_ROUTER_APP_NAME].app_status.current == "error",
        timeout=300,
    )


@markers.arm64_only
def test_amd_charm_on_arm_host(juju: jubilant_backports.Juju, ubuntu_base) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = "./mysql-router-k8s_ubuntu@22.04-amd64.charm"

    resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        num_units=1,
        resources=resources,
        base=ubuntu_base,
    )

    juju.wait(
        ready=lambda status: status.apps[MYSQL_ROUTER_APP_NAME].app_status.current == "error",
        timeout=300,
    )


# TODO: add s390x test
