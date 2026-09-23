# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test that removing the backend-database relation does not fail any hook."""

import logging

import jubilant_backports
from jubilant_backports import Juju

from ..helpers_new import (
    METADATA,
    MINUTE_SECS,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"


def _app_status_message(status, app_name: str, message: str) -> bool:
    """Whether the app's status message contains the given text."""
    app = status.apps.get(app_name)
    return app is not None and message in app.app_status.message


def test_backend_database_relation_removal(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Remove the backend-database relation; the router must not go to error.

    During relation teardown the router must not contact MySQL (which may be
    unreachable) or try to re-bootstrap, so the hook must succeed and the
    unit must report the missing relation, not go into error.
    """
    router_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logging.info("Deploying mysql-k8s and mysql-router-k8s")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/edge",
        config={"profile": "testing"},
        num_units=3,
        trust=True,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        resources=router_resources,
        num_units=1,
        trust=True,
    )

    logging.info("Integrating mysql-k8s with mysql-router-k8s")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )

    logging.info("Waiting for mysql-k8s to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_SERVER_APP_NAME),
        error=jubilant_backports.any_error,
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    # With no client application related to its `database` endpoint, the router
    # bootstraps against MySQL and then blocks on the missing client relation.
    logging.info("Waiting for mysql-router-k8s to block on the missing client relation")
    juju.wait(
        ready=lambda status: _app_status_message(
            status, MYSQL_ROUTER_APP_NAME, "Missing relation: database"
        ),
        error=jubilant_backports.any_error,
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    logging.info("Removing the backend-database relation")
    juju.remove_relation(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )

    # Router now must report the missing backend-database relation instead.
    logging.info("Waiting for mysql-router-k8s to block on the missing backend-database relation")
    juju.wait(
        ready=lambda status: _app_status_message(
            status, MYSQL_ROUTER_APP_NAME, "Missing relation: backend-database"
        ),
        error=jubilant_backports.any_error,
        timeout=10 * MINUTE_SECS,
        delay=5.0,
    )

    logging.info("Re-integrating mysql-k8s with mysql-router-k8s")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )

    # The router must recover (re-bootstrap) once the relation is re-established.
    logging.info("Waiting for mysql-router-k8s to recover")
    juju.wait(
        ready=lambda status: _app_status_message(
            status, MYSQL_ROUTER_APP_NAME, "Missing relation: database"
        ),
        error=jubilant_backports.any_error,
        timeout=10 * MINUTE_SECS,
        delay=5.0,
    )
