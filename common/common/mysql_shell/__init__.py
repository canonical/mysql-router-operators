# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""MySQL Shell in Python execution mode

https://dev.mysql.com/doc/mysql-shell/8.0/en/
"""

import dataclasses
import logging

from mysql_shell.builders import QueryQuoter
from mysql_shell.executors import BaseExecutor
from mysql_shell.clients import ClusterClient, InstanceClient
from mysql_shell.models import User
from mysql_shell_contrib.builders import CharmAuthorizationQueryBuilder

from .. import utils

_ROLE_DML = "charmed_dml"
_ROLE_READ = "charmed_read"
_ROLE_MAX_LENGTH = 32

logger = logging.getLogger(__name__)


@dataclasses.dataclass(kw_only=True)
class RouterUserInformation:
    """MySQL Router user information"""

    username: str
    router_id: str


class Shell:
    """MySQL Shell connected to MySQL cluster"""

    def __init__(self, executor: BaseExecutor) -> None:
        """Initialize the shell connection"""
        self._executor = executor
        self._cluster_client = ClusterClient(self._executor)
        self._instance_client = InstanceClient(self._executor, QueryQuoter())

    @property
    def username(self):
        return self._executor.connection_details.username

    def _get_attributes(self, additional_attributes: dict | None = None) -> dict:
        """Attributes for (MySQL) users created by this charm

        If the relation with the MySQL charm is broken, the MySQL charm will use this attribute
        to delete all users created by this charm.
        """
        attributes = {"created_by_user": self.username}
        if additional_attributes:
            attributes.update(additional_attributes)

        return attributes

    def _get_mysql_databases(self) -> set[str]:
        """Returns a set with the MySQL databases."""
        logger.debug("Getting MySQL databases")
        databases = self._instance_client.search_instance_databases("%")
        logger.debug(f"MySQL databases found: {len(databases)}")

        return {db for db in databases}

    def _get_mysql_roles(self, name_pattern: str) -> set[str]:
        """Returns a set with the MySQL roles."""
        logger.debug(f"Getting MySQL roles with {name_pattern=}")
        roles = self._instance_client.search_instance_roles("%")
        logger.debug(f"MySQL roles found for {name_pattern=}: {len(roles)}")

        return {role.rolename for role in roles}

    def _build_application_database_dba_role(self, database: str) -> str:
        """Builds the database-level DBA role, given length constraints."""
        role_prefix = "charmed_dba"
        role_suffix = "XX"

        role_name_available = _ROLE_MAX_LENGTH - len(role_prefix) - len(role_suffix) - 2
        role_name_description = database[:role_name_available]
        role_name_collisions = self._get_mysql_roles(f"{role_prefix}_{role_name_description}_%")

        return "_".join((
            role_prefix,
            role_name_description,
            str(len(role_name_collisions)).zfill(len(role_suffix)),
        ))

    def _create_application_database(self, *, database: str) -> None:
        """Create database for related database_provides application."""
        if database in self._get_mysql_databases():
            return

        query_builder = CharmAuthorizationQueryBuilder(
            role_admin="",
            role_backup="",
            role_ddl="",
            role_stats="",
            role_reader=_ROLE_READ,
            role_writer=_ROLE_DML,
        )

        role_name = self._build_application_database_dba_role(database)
        mysql_roles = self._get_mysql_roles("charmed_%")

        create_queries = [query_builder.build_database_admin_role_query(role_name, database)]
        update_queries = []
        if _ROLE_READ in mysql_roles:
            update_queries += [query_builder.build_instance_reader_role_update_query(database)]
        if _ROLE_DML in mysql_roles:
            update_queries += [query_builder.build_instance_writer_role_update_query(database)]

        queries = ";".join((
            *create_queries,
            *update_queries,
        ))

        logger.debug(f"Creating {database=}")
        self._instance_client.create_instance_database(database)
        self._executor.execute_sql(queries)
        logger.debug(f"Created {database=}")

    def _create_application_user(self, *, database: str, username: str, password: str) -> str:
        """Create database user for related database_provides application."""
        user = User(username)
        attrs = self._get_attributes()

        queries = ";".join((
            f"GRANT USAGE ON *.* TO `{username}`",
            f"GRANT ALL PRIVILEGES ON `{database}`.* TO `{username}`",
        ))

        logger.debug(f"Creating {username=} with {attrs=}")
        self._instance_client.create_instance_user(user, password=password)
        self._instance_client.update_instance_user(user, password=password, attrs=attrs)
        self._executor.execute_sql(queries)
        logger.debug(f"Created {username=} with {attrs=}")

        return password

    def create_application_database(self, *, database: str, username: str) -> str:
        """Create both the database and the relation user, returning its password."""
        password = utils.generate_password()
        self._create_application_database(database=database)
        self._create_application_user(database=database, username=username, password=password)

        return password

    def add_attributes_to_mysql_router_user(
        self, *, username: str, router_id: str, unit_name: str
    ) -> None:
        """Add attributes to user created during MySQL Router bootstrap."""
        user = User(username)
        attrs = self._get_attributes({
            "router_id": router_id,
            "created_by_juju_unit": unit_name,
        })

        logger.debug(f"Adding {attrs=} to {username=}")
        self._instance_client.update_instance_user(user, attrs=attrs)
        logger.debug(f"Added {attrs=} to {username=}")

    def get_mysql_router_user_for_unit(self, unit_name: str) -> RouterUserInformation | None:
        """Get MySQL Router user created by a previous instance of the unit.

        Get username & router ID attribute.

        Before container restart, the charm does not have an opportunity to delete the MySQL
        Router user or cluster metadata created during MySQL Router bootstrap. After container
        restart, the user and cluster metadata should be deleted before bootstrapping MySQL Router
        again.
        """
        logger.debug(f"Getting MySQL Router user for {unit_name=}")
        users = self._instance_client.search_instance_users(
            name_pattern="%",
            attrs={
                "created_by_user": self.username,
                "created_by_juju_unit": unit_name,
            },
        )

        if not users:
            logger.debug(f"No MySQL Router user found for {unit_name=}")
            return

        logger.debug(f"MySQL Router user found for {unit_name=}")
        return RouterUserInformation(
            username=users[0].username,
            router_id=users[0].attributes["router_id"],
        )

    def get_routers_in_cluster_set(self) -> set[str]:
        """Get MySQL Router instances in the current InnoDB ClusterSet."""
        logger.debug(f"Getting MySQL Routers in cluster set")
        output = self._cluster_client.list_cluster_set_routers()
        logger.debug(f"MySQL Routers found for cluster set")

        return {router for router in output["routers"].keys()}

    def delete_user(self, username: str) -> None:
        """Delete user."""
        user = User(username)

        logger.debug(f"Deleting {username=}")
        self._instance_client.delete_instance_user(user)
        logger.debug(f"Deleted {username=}")
