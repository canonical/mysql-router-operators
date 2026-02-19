#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant_backports
import pytest

from .helpers import (
    APPLICATION_DEFAULT_APP_NAME,
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    delete_file_or_directory_in_unit,
    ls_la_in_unit,
    read_contents_from_file_in_unit,
    stop_running_flush_mysqlrouter_cronjobs,
    write_content_to_file_in_unit,
)

logger = logging.getLogger(__name__)

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
APPLICATION_APP_NAME = APPLICATION_DEFAULT_APP_NAME
CHARMED_MYSQL_COMMON_DIRECTORY = "/var/snap/charmed-mysql/common"
SLOW_TIMEOUT = 15 * 60


@pytest.mark.abort_on_fail
def test_log_rotation(juju: jubilant_backports.Juju, charm, ubuntu_base) -> None:
    """Test the log rotation of mysqlrouter logs."""
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
    )
    juju.deploy(
        APPLICATION_APP_NAME,
        app=APPLICATION_APP_NAME,
        num_units=1,
        # MySQL Router is subordinate—it will use the series of the principal charm
        base=ubuntu_base,
        channel="latest/edge",
    )

    unit_name = f"{APPLICATION_APP_NAME}/0"

    logger.info("Relating mysqlrouter with mysql-test-app")

    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:database", f"{APPLICATION_APP_NAME}:database")

    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")

    juju.wait(
        ready=lambda status: (
            status.apps[MYSQL_APP_NAME].app_status == "active"
            and status.apps[MYSQL_ROUTER_APP_NAME].app_status == "active"
            and status.apps[APPLICATION_APP_NAME].app_status == "active"
        ),
        timeout=SLOW_TIMEOUT,
    )

    logger.info("Removing the cron file")
    delete_file_or_directory_in_unit(juju, unit_name, "/etc/cron.d/flush_mysqlrouter_logs")

    logger.info("Stopping any running logrotate jobs")
    stop_running_flush_mysqlrouter_cronjobs(juju, unit_name)

    logger.info("Removing existing archive directory")
    delete_file_or_directory_in_unit(
        juju,
        unit_name,
        f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter/",
    )

    logger.info("Writing some data mysqlrouter log file")
    log_path = f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/mysqlrouter.log"
    write_content_to_file_in_unit(juju, unit_name, log_path, "test mysqlrouter content\n")

    logger.info("Ensuring only log files exist")
    ls_la_output = ls_la_in_unit(
        juju, unit_name, f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/"
    )

    assert len(ls_la_output) == 1, f"❌ files other than log files exist {ls_la_output}"
    directories = [line.split()[-1] for line in ls_la_output]
    assert directories == ["mysqlrouter.log"], (
        f"❌ file other than logs files exist: {ls_la_output}"
    )

    logger.info("Executing logrotate")
    juju.ssh(
        target=unit_name,
        command=(
            "sudo "
            "-u "
            "snap_daemon "
            "logrotate "
            "-f "
            "-s "
            "/tmp/logrotate.status "
            "/etc/logrotate.d/flush_mysqlrouter_logs"
        ),
    )

    logger.info("Ensuring log files and archive directories exist")
    ls_la_output = ls_la_in_unit(
        juju, unit_name, f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/"
    )

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
        juju, unit_name, f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/mysqlrouter.log"
    )
    assert "test mysqlrouter content" not in file_contents, (
        "❌ log file mysqlrouter.log not rotated"
    )

    ls_la_output = ls_la_in_unit(
        juju,
        unit_name,
        f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter/",
    )
    assert len(ls_la_output) == 1, f"❌ more than 1 file in archive directory: {ls_la_output}"

    filename = ls_la_output[0].split()[-1]
    file_contents = read_contents_from_file_in_unit(
        juju,
        unit_name,
        f"{CHARMED_MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter/{filename}",
    )
    assert "test mysqlrouter content" in file_contents, "❌ log file mysqlrouter.log not rotated"
