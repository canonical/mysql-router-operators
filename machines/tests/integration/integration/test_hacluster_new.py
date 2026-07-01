# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess

import jubilant_backports
from jubilant_backports import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import architecture, juju_
from ..helpers import execute_queries_against_unit
from ..helpers_new import (
    MINUTE_SECS,
    get_app_leader,
    get_app_units,
    get_unit_certificate_issuer,
    wait_for_apps_status,
    wait_for_unit_message,
    wait_for_unit_status,
)

DATA_INTEGRATOR_APP_NAME = "data-integrator"
HA_CLUSTER_APP_NAME = "hacluster"
MYSQL_ROUTER_APP_NAME = "mysql-router"
MYSQL_SERVER_APP_NAME = "mysql"

MYSQL_ROUTER_VIP = None
MYSQL_ROUTER_SOCKET = "/var/snap/charmed-mysql/common/run/mysqlrouter/mysql.sock"
TEST_DATABASE_NAME = "test_database"

if juju_.is_3_or_higher:
    TLS_APP_NAME = "self-signed-certificates"
    TLS_APP_BASE = "ubuntu@24.04"
    TLS_APP_CHANNEL = "1/stable"
    TLS_APP_CONFIG = {"ca-common-name": "Test CA"}
else:
    TLS_APP_NAME = "tls-certificates-operator"
    TLS_APP_BASE = "ubuntu@22.04"
    TLS_APP_CHANNEL = "legacy/edge" if architecture.architecture == "arm64" else "legacy/stable"
    TLS_APP_CONFIG = {"ca-common-name": "Test CA", "generate-self-signed-certificates": "true"}


def test_external_connectivity_with_ha_cluster(juju: Juju, charm: str, ubuntu_base: str) -> None:
    """Test external connectivity with data-integrator ha-cluster."""
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
        charm=DATA_INTEGRATOR_APP_NAME,
        app=DATA_INTEGRATOR_APP_NAME,
        base=ubuntu_base,
        channel="latest/stable",
        config={"database-name": TEST_DATABASE_NAME},
        num_units=4,
    )

    logging.info("Relating the applications")
    juju.integrate(
        f"{MYSQL_SERVER_APP_NAME}:database",
        f"{MYSQL_ROUTER_APP_NAME}:backend-database",
    )
    juju.integrate(
        f"{DATA_INTEGRATOR_APP_NAME}:mysql",
        f"{MYSQL_ROUTER_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            DATA_INTEGRATOR_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_SERVER_APP_NAME,
        ),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)
    data_integrator_creds = juju.run(unit=data_integrator_leader, action="get-credentials")

    mysql_user = data_integrator_creds.results["mysql"]["username"]
    mysql_pass = data_integrator_creds.results["mysql"]["password"]
    mysql_host = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[0]
    mysql_port = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[1]

    databases = execute_queries_against_unit(
        username=mysql_user,
        password=mysql_pass,
        host=mysql_host,
        port=mysql_port,
        queries=["SHOW DATABASES;"],
    )

    logging.info("Ensure the database is accessible externally")
    assert TEST_DATABASE_NAME in databases

    logging.info("Ensure provided host in a data-integrator ip")
    assert mysql_host in [
        get_unit_machine_address(juju, DATA_INTEGRATOR_APP_NAME, unit)
        for unit in get_app_units(juju, DATA_INTEGRATOR_APP_NAME)
    ]

    logging.info("Deploying HACluster")
    juju.deploy(
        charm=HA_CLUSTER_APP_NAME,
        app=HA_CLUSTER_APP_NAME,
        base=ubuntu_base,
        channel="2.4/stable",
        num_units=1,
    )

    logging.info("Relating HACluster")
    juju.integrate(
        f"{DATA_INTEGRATOR_APP_NAME}:juju-info",
        f"{HA_CLUSTER_APP_NAME}:juju-info",
    )
    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:ha",
        f"{HA_CLUSTER_APP_NAME}:ha",
    )

    global MYSQL_ROUTER_VIP
    MYSQL_ROUTER_VIP = generate_next_available_ip(juju, mysql_host, [])

    logging.info("Configuring MySQL Router VIP")
    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"vip": MYSQL_ROUTER_VIP},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
        delay=5.0,
    )

    logging.info("Ensuring MySQL Server is accessible via VIP")
    check_server_accessible_virtual_ip(juju, MYSQL_ROUTER_VIP)

    MYSQL_ROUTER_VIP = generate_next_available_ip(juju, mysql_host, [MYSQL_ROUTER_VIP])

    logging.info("Configuring MySQL Router VIP")
    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"vip": MYSQL_ROUTER_VIP},
    )
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=10 * MINUTE_SECS,
        delay=5.0,
    )

    logging.info("Ensuring MySQL Server is accessible via VIP")
    check_server_accessible_virtual_ip(juju, MYSQL_ROUTER_VIP)


def test_ha_cluster_failover(juju: Juju, ubuntu_base: str) -> None:
    """Test the failover of the ha-cluster leader."""
    ha_cluster_leader = get_app_leader(juju, HA_CLUSTER_APP_NAME)
    ha_cluster_address = get_unit_machine_address(juju, HA_CLUSTER_APP_NAME, ha_cluster_leader)

    logging.info("Stopping HACluster LXC container")
    subprocess.check_call(["lxc", "stop", ha_cluster_address])

    logging.info("Waiting till machine is stopped")
    juju.wait(
        ready=lambda status: status.model.model_status.current == "unknown",
        timeout=10 * MINUTE_SECS,
        successes=1,
    )

    global MYSQL_ROUTER_VIP

    logging.info("Ensuring MySQL Server is accessible via VIP")
    check_server_accessible_virtual_ip(juju, MYSQL_ROUTER_VIP)

    logging.info("Starting HACluster LXC container")
    subprocess.check_call(["lxc", "start", ha_cluster_address])

    logging.info("Waiting till machine is stopped")
    juju.wait(
        ready=lambda status: status.model.model_status.current != "unknown",
        timeout=10 * MINUTE_SECS,
        successes=1,
    )


def test_router_certificates(juju: Juju) -> None:
    """Test the certificates of the MySQL Router application."""
    router_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)
    router_issuer = get_unit_certificate_issuer(juju, router_leader, MYSQL_ROUTER_SOCKET)
    assert "CN = MySQL_Router_Auto_Generated_CA_Certificate" in router_issuer

    logging.info("Deploying TLS")
    juju.deploy(
        charm=TLS_APP_NAME,
        app=TLS_APP_NAME,
        base=TLS_APP_BASE,
        channel=TLS_APP_CHANNEL,
        config=TLS_APP_CONFIG,
        num_units=1,
    )

    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, TLS_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    global MYSQL_ROUTER_VIP

    logging.info("Ensuring MySQL Server is accessible via VIP")
    check_server_accessible_virtual_ip(juju, MYSQL_ROUTER_VIP)

    logging.info("Relating TLS application")
    juju.integrate(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN = Test CA" in (
                get_unit_certificate_issuer(juju, router_leader, MYSQL_ROUTER_SOCKET)
            )

    logging.info("Unrelating TLS application")
    juju.remove_relation(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            assert "CN = MySQL_Router_Auto_Generated_CA_Certificate" in (
                get_unit_certificate_issuer(juju, router_leader, MYSQL_ROUTER_SOCKET)
            )

    logging.info("Ensuring MySQL Server is accessible via VIP")
    check_server_accessible_virtual_ip(juju, MYSQL_ROUTER_VIP)


def test_router_without_vip(juju: Juju) -> None:
    """Test the lack of VIP in the MySQL Router application."""
    router_leader = get_app_leader(juju, MYSQL_ROUTER_APP_NAME)

    logging.info("Resetting MySQL Router VIP")
    juju.config(
        app=MYSQL_ROUTER_APP_NAME,
        values={"vip": ""},
    )

    expected_status = "blocked"
    expected_message = "ha integration used without vip configuration"

    juju.wait(
        ready=lambda status: all((
            wait_for_unit_status(MYSQL_ROUTER_APP_NAME, router_leader, expected_status)(status),
            wait_for_unit_message(MYSQL_ROUTER_APP_NAME, router_leader, expected_message)(status),
        )),
        timeout=5 * MINUTE_SECS,
    )

    logging.info("Unrelating HACluster")
    juju.remove_relation(
        f"{HA_CLUSTER_APP_NAME}:ha",
        f"{MYSQL_ROUTER_APP_NAME}:ha",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant_backports.all_active, MYSQL_ROUTER_APP_NAME),
        timeout=20 * MINUTE_SECS,
        delay=5.0,
    )

    global MYSQL_ROUTER_VIP

    logging.info("Ensuring that VIP is not the data-integrator endpoint hostname")
    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)
    data_integrator_creds = juju.run(unit=data_integrator_leader, action="get-credentials")

    mysql_host = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[0]
    assert mysql_host != MYSQL_ROUTER_VIP


def check_server_accessible_virtual_ip(juju: Juju, vip: str) -> None:
    """Check whether the MySQL Server application can be accessed from the virtual IP."""
    data_integrator_leader = get_app_leader(juju, DATA_INTEGRATOR_APP_NAME)
    data_integrator_creds = juju.run(unit=data_integrator_leader, action="get-credentials")

    mysql_user = data_integrator_creds.results["mysql"]["username"]
    mysql_pass = data_integrator_creds.results["mysql"]["password"]
    mysql_host = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[0]
    mysql_port = data_integrator_creds.results["mysql"]["endpoints"].split(",")[0].split(":")[1]
    assert mysql_host == vip

    databases = execute_queries_against_unit(
        username=mysql_user,
        password=mysql_pass,
        host=mysql_host,
        port=mysql_port,
        queries=["SHOW DATABASES;"],
    )
    assert TEST_DATABASE_NAME in databases


def generate_next_available_ip(juju: Juju, starting_ip: str, exclude_ips: list[str]) -> str:
    """Compute and return the next available IP in the model's subnet."""
    model_status = juju.status()

    all_ips = [
        get_unit_machine_address(juju, app, unit)
        for app in model_status.apps.keys()
        for unit in model_status.apps[app].units.keys()
    ]

    base = str(starting_ip.rsplit(".", 1)[0])
    octet = int(starting_ip.rsplit(".", 1)[1])

    for _ in enumerate(all_ips):
        octet += 1
        if octet > 254:
            octet = 2

        next_ip = ".".join([str(base), str(octet)])
        if next_ip not in all_ips and next_ip not in exclude_ips:
            return next_ip

    raise ValueError("Unable to compute next available IP")


def get_unit_machine_address(juju: Juju, app_name: str, unit_name: str) -> str:
    """Get the machine name for the given unit."""
    status = juju.status()
    machine_id = status.get_units(app_name)[unit_name].machine
    machine_ips = status.machines[machine_id].ip_addresses

    return machine_ips[0]
