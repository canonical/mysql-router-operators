#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
from time import sleep

import jubilant_backports
import pytest
import tenacity

from . import architecture
from .helpers import (
    MYSQL_DEFAULT_APP_NAME,
    MYSQL_ROUTER_DEFAULT_APP_NAME,
    execute_queries_against_unit,
    get_data_integrator_credentials,
    get_leader_unit,
    get_machine_address,
    get_tls_certificate_issuer,
    wait_for_apps_status,
    wait_for_unit_message,
    wait_for_unit_status,
)

logger = logging.getLogger(__name__)

j_logger = logging.getLogger("jubilant")
j_logger.setLevel(logging.ERROR)

MYSQL_APP_NAME = MYSQL_DEFAULT_APP_NAME
MYSQL_ROUTER_APP_NAME = MYSQL_ROUTER_DEFAULT_APP_NAME
DATA_INTEGRATOR_APP_NAME = "data-integrator"
HA_CLUSTER_APP_NAME = "hacluster"
TIMEOUT = 20 * 60
SMALL_TIMEOUT = 5 * 60
TEST_DATABASE = "testdatabase"

TLS_APP_NAME = "self-signed-certificates"
TLS_CHANNEL = "1/stable"
TLS_CONFIG = {"ca-common-name": "Test CA"}
TLS_BASE = "ubuntu@24.04"

vip = None


def ensure_database_accessible_from_vip(
    juju: jubilant_backports.Juju, avoid_unit: str | None = None
) -> None:
    """Ensure that the database is access from the VIP."""
    logger.info("Ensure database accessible via VIP")
    credentials = get_data_integrator_credentials(
        juju, DATA_INTEGRATOR_APP_NAME, avoid_unit=avoid_unit
    )
    hostname = credentials["endpoints"].split(",")[0].split(":")[0]
    global vip
    assert hostname == vip, "An endpoint hostname other than VIP returned"

    databases = execute_queries_against_unit(
        hostname,
        credentials["username"],
        credentials["password"],
        ["SHOW DATABASES;"],
        port=credentials["endpoints"].split(",")[0].split(":")[1],
    )
    assert TEST_DATABASE in databases, "Test database not externally accessible through VIP"


def generate_next_available_ip(
    juju: jubilant_backports.Juju, starting_ip: str, exclude_ips: list[str] = []
) -> str:
    """Compute and return the next available IP in the model's subnet."""
    status = juju.status()
    all_ip_addresses = []
    for app_name in status.apps.keys():
        for unit_name in status.apps[app_name].units.keys():
            all_ip_addresses.append(get_machine_address(juju, unit_name))

    base, last_octet = starting_ip.rsplit(".", 1)
    last_octet = int(last_octet)
    for _ in range(len(all_ip_addresses)):
        last_octet += 1
        if last_octet > 254:
            last_octet = 2
        addr = ".".join([base, str(last_octet)])
        if addr not in all_ip_addresses and addr not in exclude_ips:
            return addr

    assert False, "Unable to compute next available IP"


@pytest.mark.abort_on_fail
def test_external_connectivity_vip_with_hacluster(
    juju: jubilant_backports.Juju, charm, ubuntu_base
) -> None:
    """Test external connectivity and VIP with data-integrator hacluster."""
    logger.info("Deploy and relate all applications without hacluster")
    # speed up test by firing update-status more frequently (for hacluster)
    # deploy data-integrator with mysqlrouter
    juju.deploy(
        MYSQL_APP_NAME,
        channel="8.0/edge",
        config={"profile": "testing"},
        num_units=1,
        constraints={"arch": architecture.architecture},
    )
    juju.deploy(
        charm,
        app=MYSQL_ROUTER_APP_NAME,
        base=ubuntu_base,
    )
    juju.deploy(
        DATA_INTEGRATOR_APP_NAME,
        app=DATA_INTEGRATOR_APP_NAME,
        channel="latest/stable",
        base=ubuntu_base,
        config={"database-name": TEST_DATABASE},
        num_units=4,
    )

    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:backend-database", f"{MYSQL_APP_NAME}:database")
    juju.integrate(f"{DATA_INTEGRATOR_APP_NAME}:mysql", f"{MYSQL_ROUTER_APP_NAME}:database")

    logger.info("Waiting for applications to become active")
    # We can safely wait only for data-integrator to be ready,
    # given that it will only become active once all the other
    # applications are ready.
    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            DATA_INTEGRATOR_APP_NAME,
        ),
        timeout=TIMEOUT,
    )

    logger.info("Ensure the database is accessible externally")
    credentials = get_data_integrator_credentials(juju, DATA_INTEGRATOR_APP_NAME)
    hostname = credentials["endpoints"].split(",")[0].split(":")[0]
    databases = execute_queries_against_unit(
        hostname,
        credentials["username"],
        credentials["password"],
        ["SHOW DATABASES;"],
        port=credentials["endpoints"].split(",")[0].split(":")[1],
    )
    assert TEST_DATABASE in databases, "Test database not externally accessible"

    logger.info("Ensure provided host in a data-integrator ip")
    status = juju.status()
    data_integrator_ips = [
        get_machine_address(juju, unit_name)
        for unit_name in status.apps[DATA_INTEGRATOR_APP_NAME].units.keys()
    ]
    assert hostname in data_integrator_ips, "Hostname is not a data-integrator"

    logger.info("Deploy and relate hacluster")
    juju.deploy(
        HA_CLUSTER_APP_NAME,
        channel="2.4/stable",
    )

    juju.integrate(f"{DATA_INTEGRATOR_APP_NAME}:juju-info", f"{HA_CLUSTER_APP_NAME}:juju-info")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:ha", f"{HA_CLUSTER_APP_NAME}:ha")

    logger.info("Configure the VIP on mysqlrouter")
    global vip
    vip = generate_next_available_ip(juju, hostname)

    juju.config(MYSQL_ROUTER_APP_NAME, {"vip": vip})

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            credentials = get_data_integrator_credentials(juju, DATA_INTEGRATOR_APP_NAME)
            hostname = credentials["endpoints"].split(",")[0].split(":")[0]
            assert hostname == vip, "Configured VIP not in effect"

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            DATA_INTEGRATOR_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_APP_NAME,
        ),
        timeout=TIMEOUT,
    )

    ensure_database_accessible_from_vip(juju)

    logger.info("Reconfiguring the VIP")
    vip = generate_next_available_ip(juju, vip, exclude_ips=[vip])

    juju.config(MYSQL_ROUTER_APP_NAME, {"vip": vip})

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            credentials = get_data_integrator_credentials(juju, DATA_INTEGRATOR_APP_NAME)
            hostname = credentials["endpoints"].split(",")[0].split(":")[0]
            assert hostname == vip, "Reconfigured VIP not in effect"

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            DATA_INTEGRATOR_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_APP_NAME,
            HA_CLUSTER_APP_NAME,
        ),
        timeout=TIMEOUT,
    )

    logger.info("Ensure database accessible via reconfigured VIP")
    ensure_database_accessible_from_vip(juju)


@pytest.mark.abort_on_fail
def test_hacluster_failover(juju: jubilant_backports.Juju) -> None:
    """Test the failover of the hacluster leader."""
    logger.info("Stopping the lxd container for the hacluster primary")
    # Find hacluster leader unit
    hacluster_leader_unit_name = get_leader_unit(juju, HA_CLUSTER_APP_NAME) or ""

    # Get machine hostname for the leader unit
    status = juju.status()
    machine_id = status.apps[HA_CLUSTER_APP_NAME].units[hacluster_leader_unit_name].machine
    machine_hostname = status.machines[machine_id].hostname

    subprocess.check_output(["lxc", "stop", machine_hostname], encoding="utf-8")
    sleep(10)

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_APP_NAME,
            DATA_INTEGRATOR_APP_NAME,
        ),
        timeout=TIMEOUT,
    )

    logger.info("Ensuring database still accessible via VIP")
    # Find principal unit for hacluster leader (assumes data-integrator)
    if hacluster_leader_unit_name:
        avoid_unit = hacluster_leader_unit_name.replace(
            HA_CLUSTER_APP_NAME, DATA_INTEGRATOR_APP_NAME
        )
        ensure_database_accessible_from_vip(juju, avoid_unit=avoid_unit)
    else:
        ensure_database_accessible_from_vip(juju)

    logger.info("Starting stopped machine")
    subprocess.check_output(["lxc", "start", machine_hostname], encoding="utf-8")

    logger.info("Waiting all active")

    juju.wait(
        ready=wait_for_apps_status(
            jubilant_backports.all_active,
            HA_CLUSTER_APP_NAME,
            MYSQL_ROUTER_APP_NAME,
            MYSQL_APP_NAME,
            DATA_INTEGRATOR_APP_NAME,
        ),
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
def test_tls_along_with_ha_cluster(juju: jubilant_backports.Juju, base) -> None:
    """Ensure that mysqlrouter is externally accessible with TLS integration."""
    logger.info("Deploying TLS")
    juju.deploy(
        TLS_APP_NAME,
        app=TLS_APP_NAME,
        channel=TLS_CHANNEL,
        config=TLS_CONFIG,
        base=TLS_BASE,
    )

    logger.info("Ensure auto-generated TLS cert before relation with TLS")
    mysqlrouter_unit_name = f"{MYSQL_ROUTER_APP_NAME}/0"
    credentials = get_data_integrator_credentials(juju, DATA_INTEGRATOR_APP_NAME)
    [database_host, database_port] = credentials["endpoints"].split(",")[0].split(":")
    issuer = get_tls_certificate_issuer(
        juju,
        mysqlrouter_unit_name,
        host=database_host,
        port=database_port,
    )
    assert "Issuer: CN = MySQL_Router_Auto_Generated_CA_Certificate" in issuer, (
        "Expected mysqlrouter autogenerated certificate"
    )

    logger.info("Ensure router externally accessible before TLS integration")
    ensure_database_accessible_from_vip(juju)

    logger.info("Relate TLS with MySQLRouter")
    juju.integrate(f"{MYSQL_ROUTER_APP_NAME}:certificates", f"{TLS_APP_NAME}:certificates")

    juju.wait(
        ready=lambda status: status.apps[TLS_APP_NAME].app_status == "active",
        timeout=TIMEOUT,
    )

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            issuer = get_tls_certificate_issuer(
                juju,
                mysqlrouter_unit_name,
                host=database_host,
                port=database_port,
            )
            assert "CN = Test CA" in issuer, (
                f"Expected mysqlrouter certificate from {TLS_APP_NAME}"
            )

    logger.info("Ensure router externally accessible after TLS integration")
    ensure_database_accessible_from_vip(juju)

    logger.info(f"Removing relation between mysqlrouter and {TLS_APP_NAME}")
    juju.remove_relation(
        f"{MYSQL_ROUTER_APP_NAME}:certificates",
        f"{TLS_APP_NAME}:certificates",
    )

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            issuer = get_tls_certificate_issuer(
                juju,
                mysqlrouter_unit_name,
                host=database_host,
                port=database_port,
            )
            assert "Issuer: CN = MySQL_Router_Auto_Generated_CA_Certificate" in issuer, (
                "Expected mysqlrouter autogenerated certificate"
            )

    logger.info("Ensure router externally accessible after TLS integration removed")
    ensure_database_accessible_from_vip(juju)


@pytest.mark.abort_on_fail
def test_remove_vip(juju: jubilant_backports.Juju) -> None:
    """Ensure removal of VIP results in connection through data-integrator."""
    logger.info("Resetting the VIP")
    juju.config(MYSQL_ROUTER_APP_NAME, reset="vip")

    logger.info("Waiting for mysqlrouter to be blocked due to missing VIP configuration")
    juju.wait(
        ready=wait_for_unit_status(
            MYSQL_ROUTER_APP_NAME,
            f"{MYSQL_ROUTER_APP_NAME}/0",
            "blocked",
            DATA_INTEGRATOR_APP_NAME,
        ),
        timeout=300,
    )

    juju.wait(
        ready=wait_for_unit_message(
            MYSQL_ROUTER_APP_NAME,
            f"{MYSQL_ROUTER_APP_NAME}/0",
            "ha integration used without vip configuration",
            DATA_INTEGRATOR_APP_NAME,
        ),
        timeout=60,
    )

    logger.info("Removing the relation between hacluster and mysqlrouter")
    juju.remove_relation(
        MYSQL_ROUTER_APP_NAME,
        HA_CLUSTER_APP_NAME,
    )
    juju.wait(
        ready=lambda status: status.apps[MYSQL_ROUTER_APP_NAME].app_status == "active",
        timeout=TIMEOUT,
    )

    logger.info("Ensuring that VIP is not the data-integrator endpoint hostname")
    credentials = get_data_integrator_credentials(juju, DATA_INTEGRATOR_APP_NAME)
    hostname = credentials["endpoints"].split(",")[0].split(":")[0]
    logger.info(f"Data integrator endpoint hostname is {hostname}")
    assert hostname != vip, "Hostname is VIP"
