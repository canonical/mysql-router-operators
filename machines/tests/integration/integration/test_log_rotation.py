# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import tempfile
from contextlib import suppress
from pathlib import Path

import jubilant_backports
from jubilant_backports import Juju

from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"
MYSQL_TEST_APP_NAME = "mysql-test-app"

MYSQL_COMMON_DIRECTORY = "/var/snap/charmed-mysql/common"
LOGROTATE_CRON_FILE = "/etc/cron.d/flush_mysqlrouter_logs"
LOGROTATE_CONFIG_FILE = "/etc/logrotate.d/flush_mysqlrouter_logs"
LOGROTATE_STATUS_FILE = "/tmp/logrotate.status"
TEST_DATABASE_NAME = "test_database"


def deploy_and_relate(juju: Juju, charm: str, ubuntu_base: str) -> str:
    """Deploy mysql, the router charm and mysql-test-app; return the router leader unit.

    Idempotent: if the router app already exists in the model (e.g. a previous
    test in this module deployed it; CI runs the module in a single model), the
    deployment is skipped.
    """
    if MYSQL_ROUTER_APP_NAME not in juju.status().apps:
        logging.info("Deploying all the applications")
        juju.deploy(
            charm=MYSQL_SERVER_APP_NAME,
            app=MYSQL_SERVER_APP_NAME,
            base=ubuntu_base,
            channel="8.0/edge",
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
        ready=wait_for_apps_status(jubilant_backports.all_active),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    return get_app_leader(juju, MYSQL_ROUTER_APP_NAME)


def test_log_rotation(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test log rotation."""
    router_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

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


def test_log_rotation_does_not_rotate_empty_log_files(
    juju: Juju, charm: str, ubuntu_base: str
) -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The logrotate config used `ifempty`, so even an empty mysqlrouter.log was
    rotated into a new archive file on every run of the (then force-rotating)
    cron job, up to 1440 empty files per day. Forcing rotation on an empty log
    must not create archive files: the config must carry `notifempty`. (The
    cron job separately must not force-rotate per minute, see
    test_logrotate_cron_does_not_force_rotation.)
    """
    router_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Removing the cron file")
    delete_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=LOGROTATE_CRON_FILE,
    )

    logging.info("Removing existing archive directory and emptying the router log file")
    delete_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter",
    )
    juju.ssh(
        target=router_leader,
        command=f"sudo truncate -s 0 {MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/mysqlrouter.log",
    )

    logging.info("Forcing logrotate on the empty log file")
    juju.ssh(
        target=router_leader,
        command=f"sudo -u snap_daemon logrotate -f -s {LOGROTATE_STATUS_FILE} {LOGROTATE_CONFIG_FILE}",
    )

    logging.info("Ensuring the empty log file was not rotated")
    file_list = list_unit_files(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter",
    )
    file_names = [line.split()[-1] for line in file_list]
    assert "mysqlrouter.log" in file_names, f"mysqlrouter.log missing: {file_names}"
    rotated = [name for name in file_names if name.startswith("mysqlrouter.log-")]
    assert not rotated, f"Empty log file was rotated: {rotated}"
    with suppress(jubilant_backports.CLIError):
        archive_files = list_unit_files(
            juju=juju,
            unit_name=router_leader,
            file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter",
        )
        # `createolddir` may create the (empty) archive directory even when
        # nothing is rotated; what must not happen is a new rotated FILE.
        assert not archive_files, f"Empty log file was rotated into: {archive_files}"


def test_logrotate_cron_does_not_force_rotation(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The cron job used to run `logrotate -f` every minute, forcing rotation on
    every run regardless of the `hourly` directive and the log content (and
    firing `kill -HUP` at mysqlrouter on every rotation). Rotation must be
    driven by the config (`hourly`, `notifempty`), so the shipped cron job
    must not pass `-f` and must run hourly, not per minute.
    """
    logging.info("Reading the shipped logrotate cron configuration")
    # The cron file on the unit is deleted by other tests in this module, and
    # charm hooks only write it on (re)configuration, so assert on the charm
    # source (the tree this charm was packed from).
    source = (Path(__file__).parents[3] / "src" / "machine_logrotate.py").read_text()

    assert "logrotate" in source, "logrotate cron setup not found in the shipped charm"
    cron_line = next(
        line
        for line in source.splitlines()
        if line.strip().startswith((
            '"* * * * * snap_daemon logrotate',
            '"0 * * * * snap_daemon logrotate',
        ))
    )
    assert " -f" not in cron_line, "logrotate cron job must not force rotation with -f"
    assert cron_line.strip().startswith('"0 '), (
        "logrotate cron job must run hourly (minutes field must be 0, not *)"
    )


def test_log_rotation_retention_is_bounded(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The logrotate config used to retain 7 days of files, hoarding archive
    files that get scraped by log forwarders. The archive only needs to flush
    content to Loki, so retention must be bounded to a few hours' worth of
    files (`rotate 24`).
    """
    router_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Removing the cron file")
    delete_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=LOGROTATE_CRON_FILE,
    )

    logging.info("Removing existing archive directory")
    delete_unit_file(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter",
    )

    logging.info("Backfilling 30 rotated files, one per minute, as logrotate would create them")
    juju.ssh(
        target=router_leader,
        command=(
            "sudo -u snap_daemon bash -c '"
            f"mkdir -p {MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter && "
            "for i in $(seq 1 30); do "
            'ts=$(date -d "$i minutes ago" +%Y%m%d_%H:%M); '
            f'touch -d "$i minutes ago" "{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter/mysqlrouter.log-$ts"; '
            "done'"
        ),
    )

    logging.info("Writing content to the router log file so that rotation actually happens")
    juju.ssh(
        target=router_leader,
        command=(
            f"sudo -u snap_daemon bash -c 'echo test-retention-content > "
            f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/mysqlrouter.log'"
        ),
    )

    logging.info("Executing logrotate")
    juju.ssh(
        target=router_leader,
        command=f"sudo -u snap_daemon logrotate -f -s {LOGROTATE_STATUS_FILE} {LOGROTATE_CONFIG_FILE}",
    )

    file_list = list_unit_files(
        juju=juju,
        unit_name=router_leader,
        file_path=f"{MYSQL_COMMON_DIRECTORY}/var/log/mysqlrouter/archive_mysqlrouter",
    )
    file_names = [line.split()[-1] for line in file_list]
    # 30 backfilled files + 1 fresh rotation = 31; `rotate 24` must prune to 24.
    assert len(file_names) == 24, (
        f"Archive retention is not bounded to rotate 24: {len(file_names)} files kept"
    )


def delete_unit_file(juju: Juju, unit_name: str, file_path: str) -> None:
    """Delete a path in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The unit on which to delete the file
        file_path: The path or file to delete
    """
    if file_path.strip() in ["/", "."]:
        return

    with suppress(jubilant_backports.CLIError):
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
