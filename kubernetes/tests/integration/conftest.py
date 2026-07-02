# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from collections.abc import Generator

import jubilant
import pytest

from . import architecture
from .helpers_new import get_app_leader

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)

MYSQL_TEST_APP_NAME = "mysql-test-app"


def pytest_addoption(parser):
    """Adds command line parameter ``--model`` (see help for details)."""
    parser.addoption(
        "--model",
        action="store",
        default="testing",
        help="model name or ':auto:' for temporary model, default to 'testing'",
        required=False,
    )


@pytest.fixture
def charm():
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./mysql-router-k8s_ubuntu@26.04-{architecture.architecture}.charm"


@pytest.fixture
def continuous_writes(juju: jubilant.Juju) -> Generator:
    """Starts continuous writes to the MySQL cluster for a test and clear the writes at the end."""
    test_app_leader = get_app_leader(juju, MYSQL_TEST_APP_NAME)

    logging.info("Clearing continuous writes")
    juju.run(test_app_leader, "clear-continuous-writes")
    logging.info("Starting continuous writes")
    juju.run(test_app_leader, "start-continuous-writes")

    yield

    logging.info("Clearing continuous writes")
    juju.run(test_app_leader, "clear-continuous-writes")


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    """Pytest fixture that yields a new `jubilant.Juju` object."""
    model = request.config.getoption("--model")

    if model == ":auto:":
        with jubilant.temp_model() as juju:
            yield juju
    else:
        yield jubilant.Juju(model=model)
