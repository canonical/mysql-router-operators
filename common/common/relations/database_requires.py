# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Relation to MySQL charm"""

import logging
import typing

import charm_ as charm
import ops

from .. import status_exception
from .._charm_libs.charms.data_platform_libs.v0 import data_interfaces
from . import remote_databag

if typing.TYPE_CHECKING:
    from .. import abstract_charm

logger = logging.getLogger(__name__)


class _MissingRelation(status_exception.StatusException):
    """Relation to MySQL charm does (or will) not exist for this unit

    If this unit is tearing down, the relation could still exist for other units.
    """

    def __init__(self, *, endpoint_name: str) -> None:
        super().__init__(ops.BlockedStatus(f"Missing relation: {endpoint_name}"))


class _RelationBreaking(_MissingRelation):
    """Relation to MySQL charm will be broken for this unit after the current event is handled

    Relation currently exists

    If this unit is tearing down, the relation could still exist for other units.
    """


class ConnectionInformation:
    """Information for connection to MySQL cluster"""

    host: str
    port: str
    username: str
    password: str


class RedactedConnectionInformation(ConnectionInformation):
    """Connection information with redacted password

    Used for logging
    """

    def __init__(self, *, host: str, port: str, username: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = "***"


class CompleteConnectionInformation(ConnectionInformation):
    """Information for connection to MySQL cluster

    User has permission to:
    - Create databases & users
    - Grant all privileges on a database to a user
    (Different from user that MySQL Router runs with after bootstrap.)
    """

    def __init__(
        self, *, interface: data_interfaces.DatabaseRequires, charm_: ops.CharmBase
    ) -> None:
        endpoint_name = interface.relation_name

        # Needed because of breaking change in ops 2.10
        # https://github.com/canonical/operator/pull/1091
        # Breaking relations included to clean up users during relation-broken event
        # Recommended approach from Charm Tech team:
        # https://github.com/canonical/operator/issues/1279#issuecomment-2921130420
        # Use Juju event (`charm.event`) instead of ops event so that breaking relation is included
        # in ops deferred or custom events, as recommended by Charm Tech team
        if isinstance(
            charm.event, charm.RelationBrokenEvent
        ) and charm.event.endpoint == charm.Endpoint(endpoint_name):
            relations = [
                *interface.relations,
                charm_.model.get_relation(
                    relation_name=endpoint_name, relation_id=charm.event.relation.id
                ),
            ]
        else:
            relations = interface.relations

        if not relations:
            raise _MissingRelation(endpoint_name=endpoint_name)
        assert len(relations) == 1
        relation = relations[0]
        if not relation.active:
            # Relation will be broken after the current event is handled
            raise _RelationBreaking(endpoint_name=endpoint_name)
        # MySQL charm databag
        databag = remote_databag.RemoteDatabag(interface=interface, relation=relation)
        endpoints = databag["endpoints"].split(",")
        assert len(endpoints) == 1
        endpoint = endpoints[0]
        self.host = endpoint.split(":")[0]
        self.port = endpoint.split(":")[1]
        self.username = databag["username"]
        self.password = databag["password"]

    @property
    def redacted(self):
        """Connection information with redacted password"""
        return RedactedConnectionInformation(
            host=self.host, port=self.port, username=self.username
        )


class RelationEndpoint:
    """Relation endpoint for MySQL charm"""

    _NAME = "backend-database"

    def __init__(self, charm_: "abstract_charm.MySQLRouterCharm") -> None:
        self._charm = charm_
        self._interface = data_interfaces.DatabaseRequires(
            self._charm,
            relation_name=self._NAME,
            # Database name disregarded by MySQL charm if "mysqlrouter" extra user role requested
            database_name="mysql_innodb_cluster_metadata",
            extra_user_roles="mysqlrouter",
        )
        self._charm.framework.observe(self._interface.on.database_created, self._charm.reconcile)
        self._charm.framework.observe(self._interface.on.endpoints_changed, self._charm.reconcile)

    @property
    def connection_info(self) -> CompleteConnectionInformation | None:
        """Information for connection to MySQL cluster"""
        try:
            return CompleteConnectionInformation(interface=self._interface, charm_=self._charm)
        except (_MissingRelation, remote_databag.IncompleteDatabag):
            return None

    @property
    def is_relation_breaking(self) -> bool:
        """Whether relation will be broken after the current event is handled"""
        try:
            CompleteConnectionInformation(interface=self._interface, charm_=self._charm)
        except _RelationBreaking:
            return True
        except (_MissingRelation, remote_databag.IncompleteDatabag):
            pass
        return False

    @property
    def status(self) -> ops.StatusBase | None:
        """Report non-active status."""
        try:
            CompleteConnectionInformation(interface=self._interface, charm_=self._charm)
        except (_MissingRelation, remote_databag.IncompleteDatabag) as exception:
            return exception.status

    def does_relation_exist(self) -> bool:
        """Whether a relation exists

        From testing: during scale up, this should return `True` as soon as this unit receives the
        first relation-created event on any endpoint
        """
        return charm.Endpoint(self._NAME).relation is not None
