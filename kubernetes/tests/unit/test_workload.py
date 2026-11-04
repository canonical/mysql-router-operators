# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest.mock

import common.workload
import pytest
import requests


@pytest.mark.parametrize(
    "config_file_text,username",
    [
        (
            """[DEFAULT]
name = system
keyring_path = /var/snap/charmed-mysql/64/var/lib/mysqlrouter/keyring
master_key_path = /var/snap/charmed-mysql/64/etc/mysqlrouter/mysqlrouter.key
connect_timeout = 5
read_timeout = 30
dynamic_state = /var/snap/charmed-mysql/64/var/lib/mysqlrouter/state.json
client_ssl_cert = /var/snap/charmed-mysql/64/var/lib/mysqlrouter/router-cert.pem
client_ssl_key = /var/snap/charmed-mysql/64/var/lib/mysqlrouter/router-key.pem
client_ssl_mode = PREFERRED
server_ssl_mode = PREFERRED
server_ssl_verify = DISABLED
unknown_config_option = error

[logger]
level = INFO

[metadata_cache:bootstrap]
cluster_type = gr
router_id = 1
user = mysql_router1_hc3hpayqp2by
ttl = 5
auth_cache_ttl = -1
auth_cache_refresh_interval = 5
use_gr_notifications = 1

[routing:bootstrap_rw]
bind_address = 127.0.0.1
bind_port = 6446
socket = /var/snap/charmed-mysql/common/run/mysqlrouter/mysql.sock
destinations = metadata-cache://cluster-set-35f57988bc107ceafce1854a03664d6b/?role=PRIMARY
routing_strategy = first-available
protocol = classic

[routing:bootstrap_ro]
bind_address = 127.0.0.1
bind_port = 6447
socket = /var/snap/charmed-mysql/common/run/mysqlrouter/mysqlro.sock
destinations = metadata-cache://cluster-set-35f57988bc107ceafce1854a03664d6b/?role=SECONDARY
routing_strategy = round-robin-with-fallback
protocol = classic

[routing:bootstrap_x_rw]
bind_address = 127.0.0.1
bind_port = 6448
socket = /var/snap/charmed-mysql/common/run/mysqlrouter/mysqlx.sock
destinations = metadata-cache://cluster-set-35f57988bc107ceafce1854a03664d6b/?role=PRIMARY
routing_strategy = first-available
protocol = x

[routing:bootstrap_x_ro]
bind_address = 127.0.0.1
bind_port = 6449
socket = /var/snap/charmed-mysql/common/run/mysqlrouter/mysqlxro.sock
destinations = metadata-cache://cluster-set-35f57988bc107ceafce1854a03664d6b/?role=SECONDARY
routing_strategy = round-robin-with-fallback
protocol = x

[http_server]
port = 8443
ssl = 1
ssl_cert = /var/snap/charmed-mysql/64/var/lib/mysqlrouter/router-cert.pem
ssl_key = /var/snap/charmed-mysql/64/var/lib/mysqlrouter/router-key.pem
bind_address = 127.0.0.1

[http_auth_realm:default_auth_realm]
backend = default_auth_backend
method = basic
name = default_realm

[rest_router]
require_realm = default_auth_realm

[rest_api]

[http_auth_backend:default_auth_backend]
backend = metadata_cache

[rest_routing]
require_realm = default_auth_realm

[rest_metadata_cache]
require_realm = default_auth_realm

""",
            "mysql_router1_hc3hpayqp2by",
        ),
        (
            """# File automatically generated during MySQL Router bootstrap
[DEFAULT]
name=system
keyring_path=/var/lib/mysqlrouter/keyring
master_key_path=/etc/mysqlrouter/mysqlrouter.key
connect_timeout=5
read_timeout=30
dynamic_state=/var/lib/mysqlrouter/state.json
client_ssl_cert=/var/lib/mysqlrouter/router-cert.pem
client_ssl_key=/var/lib/mysqlrouter/router-key.pem
client_ssl_mode=PREFERRED
server_ssl_mode=AS_CLIENT
server_ssl_verify=DISABLED
unknown_config_option=error

[logger]
level=INFO

[metadata_cache:bootstrap]
cluster_type=gr
router_id=1
user=mysql_router1_t0kj7qvusegl
ttl=5
auth_cache_ttl=-1
auth_cache_refresh_interval=5
use_gr_notifications=1

[routing:bootstrap_rw]
bind_address=0.0.0.0
bind_port=6446
destinations=metadata-cache://cluster-set-2137eca38547ab65bdaecb2833dedaf3/?role=PRIMARY
routing_strategy=first-available
protocol=classic

[routing:bootstrap_ro]
bind_address=0.0.0.0
bind_port=6447
destinations=metadata-cache://cluster-set-2137eca38547ab65bdaecb2833dedaf3/?role=SECONDARY
routing_strategy=round-robin-with-fallback
protocol=classic

[routing:bootstrap_x_rw]
bind_address=0.0.0.0
bind_port=6448
destinations=metadata-cache://cluster-set-2137eca38547ab65bdaecb2833dedaf3/?role=PRIMARY
routing_strategy=first-available
protocol=x

[routing:bootstrap_x_ro]
bind_address=0.0.0.0
bind_port=6449
destinations=metadata-cache://cluster-set-2137eca38547ab65bdaecb2833dedaf3/?role=SECONDARY
routing_strategy=round-robin-with-fallback
protocol=x

[http_server]
port=8443
ssl=1
ssl_cert=/var/lib/mysqlrouter/router-cert.pem
ssl_key=/var/lib/mysqlrouter/router-key.pem
bind_address=127.0.0.1

[http_auth_realm:default_auth_realm]
backend=default_auth_backend
method=basic
name=default_realm

[rest_router]
require_realm=default_auth_realm

[rest_api]

[http_auth_backend:default_auth_backend]
backend=metadata_cache

[rest_routing]
require_realm=default_auth_realm

[rest_metadata_cache]
require_realm=default_auth_realm

""",
            "mysql_router1_t0kj7qvusegl",
        ),
    ],
)
def test_parse_username_from_config(config_file_text, username):
    assert (
        common.workload.RunningWorkload._parse_username_from_config(config_file_text) == username
    )


def _workload():
    workload = object.__new__(common.workload.RunningWorkload)
    workload._container = unittest.mock.MagicMock()
    workload._cos = unittest.mock.MagicMock()
    workload._cos.MONITORING_USERNAME = "monitoring"
    return workload


def test_disable_exporter_keeps_monitoring_user():
    workload = _workload()
    with unittest.mock.patch.object(workload, "cleanup_monitoring_user") as cleanup:
        workload._disable_exporter()
    cleanup.assert_not_called()
    workload._container.update_mysql_router_exporter_service.assert_called_once_with(enabled=False)


def test_ensure_monitoring_user_creates_when_missing():
    workload = _workload()
    workload._container.mysql_router_rest_api_user_exists.return_value = False
    with unittest.mock.patch.object(workload, "setup_monitoring_user") as setup:
        workload.ensure_monitoring_user()
    workload._container.mysql_router_rest_api_user_exists.assert_called_once_with("monitoring")
    setup.assert_called_once_with()


def test_ensure_monitoring_user_noop_when_present():
    workload = _workload()
    workload._container.mysql_router_rest_api_user_exists.return_value = True
    with unittest.mock.patch.object(workload, "setup_monitoring_user") as setup:
        workload.ensure_monitoring_user()
    setup.assert_not_called()


@pytest.mark.parametrize(
    "exception",
    [requests.exceptions.HTTPError("401"), requests.exceptions.ConnectionError(), RuntimeError()],
)
def test_ensure_monitoring_user_does_not_raise_if_router_not_authenticating(exception):
    workload = _workload()
    workload._container.mysql_router_rest_api_user_exists.return_value = False
    with unittest.mock.patch.object(workload, "setup_monitoring_user", side_effect=exception):
        workload.ensure_monitoring_user()


def test_restart_stuck_router_restarts_and_waits():
    workload = _workload()
    workload._charm = unittest.mock.MagicMock()
    workload._container.mysql_router_service_enabled = True
    event = unittest.mock.Mock()
    workload.restart_stuck_router(event=event)
    workload._container.restart_mysql_router_service.assert_called_once_with()
    workload._charm.wait_until_mysql_router_ready.assert_called_once_with(event=event)
    workload._charm.set_status.assert_called_once_with(event=None)
