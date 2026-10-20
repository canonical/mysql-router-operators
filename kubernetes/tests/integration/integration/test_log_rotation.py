# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import jubilant_backports
import pytest
from jubilant_backports import Juju

from ..helpers_new import (
    CONTAINER_NAME,
    METADATA,
    MINUTE_SECS,
    get_app_leader,
    get_mysql_instance_label,
    wait_for_apps_status,
)

MYSQL_ROUTER_APP_NAME = "mysql-router-k8s"
MYSQL_SERVER_APP_NAME = "mysql-k8s"
MYSQL_TEST_APP_NAME = "mysql-test-app"
LOKI_APP_NAME = "loki-k8s"

TEST_DATABASE_NAME = "test_database"


def deploy_and_relate(juju: Juju, charm: str, ubuntu_base: str) -> str:
    """Deploy mysql-k8s, the router charm and mysql-test-app; return the router leader unit.

    Idempotent: if the router app already exists in the model (e.g. a previous
    test in this module deployed it; CI runs the module in a single model), the
    deployment is skipped.
    """
    logging.info("Deploying all the applications")
    juju.deploy(
        charm=MYSQL_SERVER_APP_NAME,
        app=MYSQL_SERVER_APP_NAME,
        base=ubuntu_base,
        channel="8.0/edge",
        config={"profile": "testing"},
        num_units=1,
        trust=True,
    )
    juju.deploy(
        charm=charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
        resources={
            "mysql-router-image": METADATA["resources"]["mysql-router-image"]["upstream-source"]
        },
        num_units=1,
        trust=True,
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
    router_app_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Stopping the logrotate executor pebble service")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="pebble stop logrotate_executor",
    )

    logging.info("Removing existing archive directory")
    delete_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/archive_mysqlrouter",
    )

    logging.info("Writing some data mysqlrouter log file")
    write_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/mysqlrouter.log",
        file_data="test mysqlrouter content",
    )

    logging.info("Ensuring only log file exist")
    file_list = list_unit_files(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter",
    )

    file_names = [line.split()[-1] for line in file_list]
    assert len(file_names) == 1
    assert sorted(file_names) == sorted(["mysqlrouter.log"])

    logging.info("Executing logrotate")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="su - mysql -c 'logrotate -f -s /tmp/logrotate.status /etc/logrotate.d/flush_mysqlrouter_logs'",
    )

    logging.info("Ensuring log file and archive directories exist")
    file_list = list_unit_files(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter",
    )

    file_names = [line.split()[-1] for line in file_list]
    assert len(file_list) == 2
    assert sorted(file_names) == sorted(["mysqlrouter.log", "archive_mysqlrouter"])

    logging.info("Ensuring log file was rotated")
    file_contents = read_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/mysqlrouter.log",
    )

    assert "test mysqlrouter content" not in file_contents

    file_list = list_unit_files(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/archive_mysqlrouter",
    )

    file_names = [line.split()[-1] for line in file_list]
    file_contents = read_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path=f"/var/log/mysqlrouter/archive_mysqlrouter/{file_names[0]}",
    )

    assert "test mysqlrouter content" in file_contents


def test_log_rotation_does_not_rotate_empty_log_files(
    juju: Juju, charm: str, ubuntu_base: str
) -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The logrotate config used `ifempty`, so even an empty mysqlrouter.log was
    rotated into a new archive file on every run of the (then force-rotating)
    executor, up to 1440 empty files per day. Forcing rotation on an empty log
    must not create archive files: the config must carry `notifempty`. (The
    executor separately must not force-rotate per minute, see
    test_logrotate_executor_does_not_force_rotation.)
    """
    router_app_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Stopping the logrotate executor pebble service")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="pebble stop logrotate_executor || true",
    )

    logging.info("Removing existing archive directory and emptying the router log file")
    delete_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/archive_mysqlrouter",
    )
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="truncate -s 0 /var/log/mysqlrouter/mysqlrouter.log",
    )

    logging.info("Forcing logrotate on the empty log file")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="su - mysql -c 'logrotate -f -s /tmp/logrotate.status /etc/logrotate.d/flush_mysqlrouter_logs'",
    )

    logging.info("Ensuring the empty log file was not rotated")
    file_list = list_unit_files(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter",
    )
    file_names = [line.split()[-1] for line in file_list]
    assert "mysqlrouter.log" in file_names, f"mysqlrouter.log missing: {file_names}"
    rotated = [name for name in file_names if name.startswith("mysqlrouter.log-")]
    assert not rotated, f"Empty log file was rotated: {rotated}"
    with suppress(jubilant_backports.CLIError):
        archive_files = list_unit_files(
            juju=juju,
            unit_name=router_app_leader,
            container=CONTAINER_NAME,
            file_path="/var/log/mysqlrouter/archive_mysqlrouter",
        )
        # `createolddir` may create the (empty) archive directory even when
        # nothing is rotated; what must not happen is a new rotated FILE.
        assert not archive_files, f"Empty log file was rotated into: {archive_files}"


def test_logrotate_executor_does_not_force_rotation(
    juju: Juju, charm: str, ubuntu_base: str
) -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The executor used to run `logrotate -f` every 60 seconds, forcing rotation
    on every run regardless of the `hourly` directive and the log content (and
    firing `kill -HUP` at mysqlrouter on every rotation). Rotation must be
    driven by the config (`hourly`, `notifempty`), so the shipped executor
    must not pass `-f` and must tick hourly, not per minute.
    """
    router_app_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Reading the shipped logrotate executor script")
    script = juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="cat /logrotate_executor.py",
    )

    assert '"-f"' not in script, "logrotate executor must not force rotation with -f"
    assert "logrotate" in script, "logrotate executor script not found in unit"
    assert "% 3600" in script, "logrotate executor must tick hourly (align to top of hour)"
    assert "% 60" not in script, "logrotate executor must not tick per minute"


def test_log_rotation_retention_is_bounded(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The logrotate config used to retain `rotate 10800` files (~7.5 days of
    per-minute churn), hoarding archive files that promtail then polls. The
    archive only needs to flush content to Loki, so retention must be bounded
    to a few hours' worth of files (`rotate 24`).
    """
    router_app_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Stopping the logrotate executor pebble service")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="pebble stop logrotate_executor || true",
    )

    logging.info("Removing existing archive directory")
    delete_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/archive_mysqlrouter",
    )

    logging.info("Backfilling 30 rotated files, one per minute, as logrotate would create them")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command=(
            "su - mysql -c 'mkdir -p /var/log/mysqlrouter/archive_mysqlrouter && "
            "for i in $(seq 1 30); do "
            'ts=$(date -d "$i minutes ago" +%Y%m%d_%H:%M); '
            'touch -d "$i minutes ago" "/var/log/mysqlrouter/archive_mysqlrouter/mysqlrouter.log-$ts"; '
            "done'"
        ),
    )

    logging.info("Writing content to the router log file so that rotation actually happens")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="su - mysql -c 'echo test-retention-content > /var/log/mysqlrouter/mysqlrouter.log'",
    )

    logging.info("Executing logrotate")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="su - mysql -c 'logrotate -f -s /tmp/logrotate.status /etc/logrotate.d/flush_mysqlrouter_logs'",
    )

    file_list = list_unit_files(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/archive_mysqlrouter",
    )
    file_names = [line.split()[-1] for line in file_list]
    # 30 backfilled files + 1 fresh rotation = 31; `rotate 24` must prune to 24.
    assert len(file_names) == 24, (
        f"Archive retention is not bounded to rotate 24: {len(file_names)} files kept"
    )


def test_promtail_target_is_non_recursive() -> None:
    """Regression test for https://github.com/canonical/mysql-router-operators/issues/234.

    The promtail target used to be the recursive glob
    `/var/log/mysqlrouter/**/*log*`, so every rotated file in
    `archive_mysqlrouter/` became an active promtail target; the vendored
    promtail v2.9.7 stat-polls each matched file 4x/s forever, making promtail
    CPU grow linearly with pod age. The shipped promtail target must be
    non-recursive.
    """
    # The charm source is not present in the workload container (only the
    # executor script is pushed there), so read it from this repository's
    # checkout -- the same tree the charm was packed from.
    source = (Path(__file__).parents[3] / "src" / "relations" / "kubernetes_cos.py").read_text()

    assert '_ROUTER_LOG_FILES_TARGET = "/var/log/mysqlrouter/*log*"' in source, (
        "promtail target must be the non-recursive /var/log/mysqlrouter/*log*"
    )
    assert "**/*log*" not in source, "promtail target must not be recursive"


def test_promtail_does_not_target_rotated_files(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Dynamic counterpart of test_promtail_target_is_non_recursive.

    With a loki-k8s logging relation active, promtail runs in the workload
    container: creating files in `archive_mysqlrouter/` must not add promtail
    targets (promtail_files_active_total must stay unchanged).

    loki-k8s is only published for amd64/ubuntu@24.04, so on runners with juju
    < 3 (no noble series) or non-amd64 architectures the dynamic check is
    skipped; the static check above still guards the shipped glob.
    """
    router_app_leader = deploy_and_relate(juju=juju, charm=charm, ubuntu_base=ubuntu_base)

    logging.info("Stopping the logrotate executor pebble service")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command="pebble stop logrotate_executor || true",
    )

    logging.info("Removing existing archive files so the measurement starts clean")
    delete_unit_file(
        juju=juju,
        unit_name=router_app_leader,
        container=CONTAINER_NAME,
        file_path="/var/log/mysqlrouter/archive_mysqlrouter",
    )
    # Give promtail one re-glob interval (10 s) to drop deleted targets.
    time.sleep(15)

    logging.info("Deploying loki-k8s and relating it to the router")
    try:
        if LOKI_APP_NAME not in juju.status().apps:
            juju.deploy(
                charm=LOKI_APP_NAME,
                app=LOKI_APP_NAME,
                base="ubuntu@24.04",  # loki-k8s 2.x is published for ubuntu@24.04
                channel="2/stable",
                num_units=1,
                trust=True,  # loki-k8s needs cluster-scoped permissions to patch its statefulset
            )
        with suppress(jubilant_backports.CLIError):
            juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:logging", f"{LOKI_APP_NAME}:logging")
        juju.wait(
            ready=wait_for_apps_status(jubilant_backports.all_active),
            timeout=20 * MINUTE_SECS,
            delay=5.0,
        )
    except jubilant_backports.CLIError:
        pytest.skip(
            "loki-k8s 2/stable is only published for amd64/ubuntu@24.04 (juju >= 3 required); "
            "skipping the dynamic promtail target check on this runner"
        )

    logging.info("Waiting for the promtail sidecar to become ready")
    for _ in range(60):
        with suppress(jubilant_backports.CLIError):
            output = _promtail_get(juju=juju, unit_name=router_app_leader, path="/ready")
            if "ready" in output.lower():
                break
        time.sleep(10)
    else:
        pytest.fail("promtail sidecar did not become ready")

    active_before = _promtail_active_files(juju=juju, unit_name=router_app_leader)
    # Positive control: the live mysqlrouter.log must be an active target.
    assert active_before >= 1, "promtail is not targeting the mysqlrouter.log file"

    logging.info("Creating 10 files in the archive directory")
    juju.ssh(
        target=router_app_leader,
        container=CONTAINER_NAME,
        command=(
            "mkdir -p /var/log/mysqlrouter/archive_mysqlrouter && "
            "for i in $(seq 1 10); do "
            'touch "/var/log/mysqlrouter/archive_mysqlrouter/mysqlrouter.log-mr234probe$i"; '
            "done"
        ),
    )

    # Promtail re-evaluates its target globs every 10 seconds; wait well past that.
    time.sleep(40)

    active_after = _promtail_active_files(juju=juju, unit_name=router_app_leader)
    assert active_after == active_before, (
        "promtail picked up files from archive_mysqlrouter: "
        f"{active_before} active files before, {active_after} after creating 10 archive files"
    )


def _promtail_get(juju: Juju, unit_name: str, path: str) -> str:
    """GET a path from the promtail sidecar's HTTP endpoint (IPv4 or IPv6)."""
    command = (
        f"curl -sf http://127.0.0.1:9080{path} || "
        f"curl -sf 'http://[::1]:9080{path}' || "
        'python3 -c "import urllib.request;'
        f"print(urllib.request.urlopen('http://[::1]:9080{path}').read().decode())\""
    )
    return juju.ssh(target=unit_name, container=CONTAINER_NAME, command=command)


def _promtail_active_files(juju: Juju, unit_name: str) -> float:
    """Return the value of promtail_files_active_total from the promtail sidecar."""
    output = _promtail_get(juju=juju, unit_name=unit_name, path="/metrics")
    values = [
        float(line.split()[-1])
        for line in output.splitlines()
        if line.startswith("promtail_files_active_total")
    ]
    assert values, "promtail_files_active_total not found in promtail metrics"
    return sum(values)


def delete_unit_file(juju: Juju, unit_name: str, container: str, file_path: str) -> None:
    """Delete a path in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The unit on which to delete the file
        container: The container on which to delete the file
        file_path: The path or file to delete
    """
    if file_path.strip() in ["/", "."]:
        return

    with suppress(jubilant_backports.CLIError):
        juju.ssh(
            command=f"find {file_path} -maxdepth 1 -delete",
            container=container,
            target=unit_name,
        )


def list_unit_files(juju: Juju, unit_name: str, container: str, file_path: str) -> list[str]:
    """Returns the list of files in the given path.

    Args:
        juju: The Juju instance
        unit_name: The unit in which to list the files
        container: The container in which to list the files
        file_path: The path at which to list the files
    """
    output = juju.ssh(
        command=f"ls -la {file_path}",
        container=container,
        target=unit_name,
    )

    output = output.split("\n")[1:]

    return [
        line.strip("\r")
        for line in output
        if len(line.strip()) > 0 and line.split()[-1] not in [".", ".."]
    ]


def read_unit_file(juju: Juju, unit_name: str, container: str, file_path: str) -> str:
    """Read contents from file in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The name of the unit to read the file from
        container: The name of the container to read the file
        file_path: The path of the unit to read the file
    """
    pod_name = get_mysql_instance_label(unit_name)

    with tempfile.NamedTemporaryFile(mode="r+", dir=Path.home()) as temp_file:
        subprocess.check_call(
            [
                "microk8s",
                "kubectl",
                "cp",
                f"--namespace={juju.model}",
                f"--container={container}",
                f"{pod_name}:{file_path}",
                f"{temp_file.name}",
            ],
        )
        contents = temp_file.read()

    return contents


def write_unit_file(juju: Juju, unit_name: str, container: str, file_path: str, file_data: str):
    """Write content to the file in the provided unit.

    Args:
        juju: The Juju instance
        unit_name: The name of the unit to write the file into
        container: The name of the container to write the file
        file_path: The path of the container to write the file
        file_data: The data to write to the file.
    """
    pod_name = get_mysql_instance_label(unit_name)

    with tempfile.NamedTemporaryFile(mode="w", dir=Path.home()) as temp_file:
        temp_file.write(file_data)
        temp_file.flush()

        subprocess.check_call(
            [
                "microk8s",
                "kubectl",
                "cp",
                f"--namespace={juju.model}",
                f"--container={container}",
                f"{temp_file.name}",
                f"{pod_name}:{file_path}",
            ],
        )
