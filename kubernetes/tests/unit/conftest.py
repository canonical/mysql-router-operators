# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import os

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled


@pytest.fixture(autouse=True)
def disable_tenacity_retry(monkeypatch):
    for retry_class in (
        "retry_if_exception",
        "retry_if_exception_type",
        "retry_if_not_exception_type",
        "retry_unless_exception_type",
        "retry_if_exception_cause_type",
        "retry_if_result",
        "retry_if_not_result",
        "retry_if_exception_message",
        "retry_if_not_exception_message",
        "retry_any",
        "retry_all",
        "retry_always",
        "retry_never",
    ):
        monkeypatch.setattr(f"tenacity.{retry_class}.__call__", lambda *args, **kwargs: False)


class _MockRefresh:
    in_progress = False
    next_unit_allowed_to_refresh = True
    workload_allowed_to_start = True
    app_status_higher_priority = None
    unit_status_higher_priority = None

    def __init__(self, _, /):
        pass

    def unit_status_lower_priority(self, *, workload_is_running=True):
        return None


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(
        "charm.KubernetesRouterCharm.wait_until_mysql_router_ready",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("common.workload.RunningWorkload._router_username", "")
    monkeypatch.setattr("common.mysql_shell.Shell._run_code", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "common.mysql_shell.Shell._get_mysql_databases", lambda *args, **kwargs: set()
    )
    monkeypatch.setattr("common.mysql_shell.Shell._get_mysql_roles", lambda *args, **kwargs: set())
    monkeypatch.setattr(
        "common.mysql_shell.Shell.get_mysql_router_user_for_unit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "common.mysql_shell.Shell.is_router_in_cluster_set", lambda *args, **kwargs: True
    )
    monkeypatch.setattr("charm_refresh.Kubernetes", _MockRefresh)
    monkeypatch.setattr(
        "charm_refresh.CharmSpecificCommon.__post_init__", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "common.relations.database_requires.RelationEndpoint.does_relation_exist",
        lambda *args, **kwargs: True,
    )

    # Can be removed when ops-scenario is updated to 8.3.0/ops[testing] 3.3.0
    # (https://github.com/canonical/operator/pull/1996)
    original_getitem = os.environ.__getitem__

    def getitem(self, key):
        if key == "JUJU_HOOK_NAME":
            if dispatch_path := self.get("JUJU_DISPATCH_PATH"):
                _, hook_name = dispatch_path.split("/")
                return hook_name.replace("_", "-")
        return original_getitem(key)

    monkeypatch.setattr("os._Environ.__getitem__", getitem)

    monkeypatch.setattr("charm_._main.Relation._other_app", lambda: os.environ["JUJU_REMOTE_APP"])


@pytest.fixture(autouse=True)
def kubernetes_patch(monkeypatch):
    monkeypatch.setattr(
        "charm.KubernetesRouterCharm.model_service_domain", "my-model.svc.cluster.local"
    )
    monkeypatch.setattr(
        "rock.Rock._run_command",
        lambda *args, **kwargs: "null",  # Use "null" for `json.loads()`
    )
    monkeypatch.setattr("rock._Path.read_text", lambda *args, **kwargs: "")
    monkeypatch.setattr("rock._Path.write_text", lambda *args, **kwargs: None)
    monkeypatch.setattr("rock._Path.unlink", lambda *args, **kwargs: None)
    monkeypatch.setattr("rock._Path.mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr("rock._Path.rmtree", lambda *args, **kwargs: None)
    monkeypatch.setattr("lightkube.Client", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "charm.KubernetesRouterCharm._reconcile_service", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "charm.KubernetesRouterCharm._get_hosts_ports",
        lambda _, port_type: "mysql-router-k8s-service.my-model.svc.cluster.local:6446"
        if port_type == "rw"
        else "mysql-router-k8s-service.my-model.svc.cluster.local:6447",
    )
    monkeypatch.setattr(
        "charm.KubernetesRouterCharm._check_service_connectivity",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "charm.KubernetesRouterCharm.get_all_k8s_node_hostnames_and_ips",
        lambda *args, **kwargs: None,
    )


@pytest.fixture(params=[True, False])
def juju_has_secrets(request, monkeypatch):
    monkeypatch.setattr("ops.JujuVersion.has_secrets", request.param)
    return request.param


@pytest.fixture(autouse=True)
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield
