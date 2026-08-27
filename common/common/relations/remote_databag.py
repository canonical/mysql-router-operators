# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Relation databag for remote application"""

import logging

import ops

from .. import status_exception
from .._charm_libs.charms.data_platform_libs.v0 import data_interfaces

logger = logging.getLogger(__name__)


class IncompleteDatabag(status_exception.StatusException):
    """Databag is missing required key"""

    def __init__(self, *, app_name: str, endpoint_name: str) -> None:
        super().__init__(
            ops.WaitingStatus(f"Waiting for {app_name} app on {endpoint_name} endpoint")
        )


class RemoteDatabag(dict):
    """Relation databag for remote application"""

    def __init__(
        self,
        interface: data_interfaces.DatabaseRequires | data_interfaces.DatabaseProvides,
        relation: ops.Relation,
    ) -> None:
        try:
            data = interface.fetch_relation_data()[relation.id]
        except KeyError:
            # Breaking relation excluded from data_interfaces lib (ops 2.10+ PR #1091).
            # Access the remote app databag directly from the relation object.
            data = relation.data[relation.app]
        super().__init__(data)
        self._app_name = relation.app.name
        self._endpoint_name = relation.name

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            logger.debug(
                f"Required {key=} missing from databag for {self._app_name=} on {self._endpoint_name=}"
            )
            raise IncompleteDatabag(app_name=self._app_name, endpoint_name=self._endpoint_name)
