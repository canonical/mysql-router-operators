# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import tempfile
from pathlib import Path

import jubilant
from jubilant import Juju

from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

MYSQL_COMMON_DIRECTORY = "/var/snap/charmed-mysql/common"
TEST_DATABASE_NAME = "test_database"


def test_log_rotation(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test log rotation."""
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.4/edge",
        config={"profile": "testing"},
        num_units=1,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        num_units=1,
    )
    juju.deploy(
        charm=MYSQL_TEST_APP_NAME,
        app=MYSQL_TEST_APP_NAME,
        base=ubuntu_base,
        channel="latest/edge",
        num_units=1,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )
    juju.integrate(
        f"{MYSQL_TEST_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    router_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)

    logging.info("Removing the cron file")
    delete_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path="/etc/cron.d/flush_mysqlrouter_logs",
    )

    logging.info("Removing existing archive directory")
    delete_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter",
    )

    logging.info("Writing some data mysqlrouter log file")
    write_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/mysqlrouter.log",
        file_data="test mysqlrouter content",
    )

    logging.info("Ensuring only log file exist")
    file_list = list_unit_files(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter",
    )

    file_names = [line.split()[-1] for line in file_list]
    assert len(file_names) == 1
    assert sorted(file_names) == sorted(["mysqlrouter.log"])

    logging.info("Executing logrotate")
    juju.ssh(
        target=router_leader,
        command="sudo -u snap_daemon logrotate -f -s /tmp/logrotate.status /etc/logrotate.d/flush_mysqlrouter_logs",
    )

    logging.info("Ensuring log file and archive directories exist")
    file_list = list_unit_files(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter",
    )

    file_names = [line.split()[-1] for line in file_list]
    assert len(file_list) == 2
    assert sorted(file_names) == sorted(["mysqlrouter.log", "archive_mysqlrouter"])

    logging.info("Ensuring log file was rotated")
    file_contents = read_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/mysqlrouter.log",
    )

    assert "test mysqlrouter content" not in file_contents

    file_list = list_unit_files(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter",
    )

    file_names = [line.split()[-1] for line in file_list]
    file_contents = read_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter/{file_names[0]}",
    )

    assert "test mysqlrouter content" in file_contents


def delete_unit_file(juju: Juju, unit_name: str, file_path: str) -> None:
    """Delete a path in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The unit on which to delete the file
        file_path: The path or file to delete
    """
    if file_path.strip() in ["/", "."]:
        return

    juju.ssh(
        command=f"sudo find {file_path} -maxdepth 1 -delete",
        target=unit_name,
    )


def list_unit_files(juju: Juju, unit_name: str, file_path: str) -> list[str]:
    """Returns the list of files in the given path.

    Args:
        juju: The Juju instance
        unit_name: The unit in which to list the files
        file_path: The path at which to list the files
    """
    output = juju.ssh(
        command=f"sudo ls -la {file_path}",
        target=unit_name,
    )

    output = output.split("\n")[1:]

    return [
        line.strip("\r")
        for line in output
        if len(line.strip()) > 0 and line.split()[-1] not in [".", ".."]
    ]


def read_unit_file(juju: Juju, unit_name: str, file_path: str) -> str:
    """Read contents from file in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The name of the unit to read the file from
        file_path: The path of the unit to read the file
    """
    temp_path = "/tmp/file"

    juju.exec(f"sudo cp {file_path} {temp_path}", unit=unit_name)
    juju.exec(f"sudo chown ubuntu:ubuntu {temp_path}", unit=unit_name)

    with tempfile.NamedTemporaryFile(mode="r+", dir=Path.home()) as temp_file:
        juju.scp(
            f"{unit_name}:{temp_path}",
            f"{temp_file.name}",
        )
        contents = temp_file.read()

    juju.exec(f"sudo rm {temp_path}", unit=unit_name)
    return contents


def write_unit_file(juju: Juju, unit_name: str, file_path: str, file_data: str):
    """Write content to the file in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The name of the unit to write the file into
        file_path: The path of the unit to write the file
        file_data: The data to write to the file.
    """
    temp_path = "/tmp/file"

    with tempfile.NamedTemporaryFile(mode="w", dir=Path.home()) as temp_file:
        temp_file.write(file_data)
        temp_file.flush()

        juju.scp(
            f"{temp_file.name}",
            f"{unit_name}:{temp_path}",
        )

    juju.exec(f"sudo mv {temp_path} {file_path}", unit=unit_name)
    juju.exec(f"sudo chown snap_daemon:snap_daemon {file_path}", unit=unit_name)
