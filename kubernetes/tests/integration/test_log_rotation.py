#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant_backports
import pytest
import yaml

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    delete_file_or_directory_in_unit,
    ls_la_in_unit,
    read_contents_from_file_in_unit,
    rotate_mysqlrouter_logs,
    stop_running_flush_mysqlrouter_job,
    stop_running_log_rotate_executor,
    wait_for_apps_status,
    write_content_to_file_in_unit,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
SLOW_TIMEOUT = 15 * 60
MODEL_CONFIG = {"logging-config": "<root>=INFO;unit=DEBUG"}


@pytest.mark.abort_on_fail
def test_log_rotation(juju: jubilant_backports.Juju, charm, ubuntu_base):
    """Test log rotation."""
    juju.model_config({"logging-config": MODEL_CONFIG["logging-config"]})

    mysqlrouter_resources = {
        "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
    }

    logger.info("Deploying mysql, mysqlrouter and application")
    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        app=MYSQL_APP_NAME,
        config={"profile": "testing"},
        base=ubuntu_base,
        num_units=3,
        trust=True,  # Necessary after a6f1f01: Fix/endpoints as k8s services (#142)
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

    logger.info("Relating mysql, mysqlrouter and application")
    # Relate the database with mysqlrouter
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
    # Relate mysqlrouter with application next
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
    logger.info("Stopping the logrotate executor pebble service")
    stop_running_log_rotate_executor(juju, unit_name)

    logger.info("Stopping any running logrotate jobs")
    stop_running_flush_mysqlrouter_job(juju, unit_name)

    logger.info("Removing existing archive directory")
    delete_file_or_directory_in_unit(
        juju,
        unit_name,
        "/var/log/mysqlrouter/archive_mysqlrouter/",
    )

    logger.info("Writing some data mysqlrouter log file")
    log_path = "/var/log/mysqlrouter/mysqlrouter.log"
    write_content_to_file_in_unit(juju, unit_name, log_path, "test mysqlrouter content\n")

    logger.info("Ensuring only log files exist")
    ls_la_output = ls_la_in_unit(juju, unit_name, "/var/log/mysqlrouter/")

    assert len(ls_la_output) == 1, f"❌ files other than log files exist {ls_la_output}"
    directories = [line.split()[-1] for line in ls_la_output]
    assert directories == ["mysqlrouter.log"], (
        f"❌ file other than logs files exist: {ls_la_output}"
    )

    logger.info("Executing logrotate")
    rotate_mysqlrouter_logs(juju, unit_name)

    logger.info("Ensuring log files and archive directories exist")
    ls_la_output = ls_la_in_unit(juju, unit_name, "/var/log/mysqlrouter/")

    assert len(ls_la_output) == 2, (
        f"❌ unexpected files/directories in log directory: {ls_la_output}"
    )
    directories = [line.split()[-1] for line in ls_la_output]
    assert sorted(directories) == sorted([
        "mysqlrouter.log",
        "archive_mysqlrouter",
    ]), f"❌ unexpected files/directories in log directory: {ls_la_output}"

    logger.info("Ensuring log files was rotated")
    file_contents = read_contents_from_file_in_unit(
        juju, unit_name, "/var/log/mysqlrouter/mysqlrouter.log"
    )
    assert "test mysqlrouter content" not in file_contents, (
        "❌ log file mysqlrouter.log not rotated"
    )

    ls_la_output = ls_la_in_unit(
        juju,
        unit_name,
        "/var/log/mysqlrouter/archive_mysqlrouter/",
    )
    assert len(ls_la_output) == 1, f"❌ more than 1 file in archive directory: {ls_la_output}"

    filename = ls_la_output[0].split()[-1]
    file_contents = read_contents_from_file_in_unit(
        juju,
        unit_name,
        f"/var/log/mysqlrouter/archive_mysqlrouter/{filename}",
    )
    assert "test mysqlrouter content" in file_contents, "❌ log file mysqlrouter.log not rotated"
