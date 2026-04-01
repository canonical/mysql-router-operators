# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
import pytest

from . import architecture, juju_
from .helpers import APPLICATION_DEFAULT_APP_NAME, get_application_name

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def juju() -> jubilant_backports.Juju:
    return jubilant_backports.Juju(model="testing")


def pytest_addoption(parser):
    """Add custom pytest command line options."""
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="keep temporarily-created models",
    )


@pytest.fixture
def ubuntu_base():
    return "ubuntu@22.04"


@pytest.fixture
def series():
    return "jammy"


@pytest.fixture
def charm(ubuntu_base):
    # Return str instead of pathlib.Path since juju deploy and juju bundle files expect
    # local charms to begin with `./` or `/` to distinguish them from Charmhub charms.
    return f"./mysql-router_{ubuntu_base}-{architecture.architecture}.charm"


@pytest.fixture
def continuous_writes(juju: jubilant_backports.Juju):
    """Starts continuous writes to the MySQL cluster for a test and clear the writes at the end."""
    application_name = get_application_name(juju, APPLICATION_DEFAULT_APP_NAME)

    application_unit = f"{application_name}/0"

    logger.info("Clearing continuous writes")
    juju_.run_action(juju, application_unit, "clear-continuous-writes")

    logger.info("Starting continuous writes")
    juju_.run_action(juju, application_unit, "start-continuous-writes")

    yield

    logger.info("Clearing continuous writes")
    juju_.run_action(juju, application_unit, "clear-continuous-writes")
