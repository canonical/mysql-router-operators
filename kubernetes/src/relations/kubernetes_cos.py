# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Relation to the cos charms."""

import logging
import typing

import common.container
import common.relations.cos
import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider

import rock

if typing.TYPE_CHECKING:
    import abstract_charm

logger = logging.getLogger(__name__)


class COSRelation(common.relations.cos.COSRelation):
    """Relation with the cos bundle."""

    _METRICS_RELATION_NAME = "metrics-endpoint"
    _LOGGING_RELATION_NAME = "logging"
    _ROUTER_LOG_FILES_TARGET = "/var/log/mysqlrouter/**/*log*"

    def __init__(
        self, charm_: "abstract_charm.MySQLRouterCharm", container_: common.container.Container
    ):
        self._grafana_dashboards = GrafanaDashboardProvider(charm_)
        self._metrics_endpoint = MetricsEndpointProvider(
            charm_,
            refresh_event=charm_.on.start,
            jobs=[{"static_configs": [{"targets": [f"*:{self._EXPORTER_PORT}"]}]}],
        )
        self._loki_push = LogProxyConsumer(
            charm_,
            relation_name=self._LOGGING_RELATION_NAME,
            logs_scheme={
                rock.CONTAINER_NAME: {
                    "log-files": [self._ROUTER_LOG_FILES_TARGET],
                },
            },
        )
        self._tracing = ops.tracing.Tracing(
            charm_, tracing_relation_name=self._TRACING_RELATION_NAME
        )

        super().__init__(charm_=charm_, container_=container_)
