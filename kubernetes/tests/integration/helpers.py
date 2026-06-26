# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import itertools

import mysql.connector
import tenacity
from mysql.connector.errors import (
    DatabaseError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)

from .connector import MySQLConnector


def execute_queries_against_unit(
    unit_address: str,
    username: str,
    password: str,
    queries: list[str],
    commit: bool = False,
) -> list:
    """Execute given MySQL queries on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the queries on
        username: The MySQL username
        password: The MySQL password
        queries: A list of queries to execute
        commit: A keyword arg indicating whether there are any writes queries

    Returns:
        A list of rows that were potentially queried
    """
    connection = mysql.connector.connect(
        host=unit_address,
        user=username,
        password=password,
    )

    cursor = connection.cursor()

    for query in queries:
        cursor.execute(query)
    if commit:
        connection.commit()

    output = list(itertools.chain(*cursor.fetchall()))

    cursor.close()
    connection.close()

    return output


@tenacity.retry(stop=tenacity.stop_after_attempt(8), wait=tenacity.wait_fixed(15), reraise=True)
def is_connection_possible(credentials: dict, **extra_opts) -> bool:
    """Test a connection to a MySQL server.

    Args:
        credentials: A dictionary with the credentials to test
        extra_opts: extra options for mysql connection
    """
    config = {
        "user": credentials["username"],
        "password": credentials["password"],
        "host": credentials["host"],
        "port": credentials["port"],
        "raise_on_warnings": False,
        "connection_timeout": 10,
        **extra_opts,
    }
    try:
        with MySQLConnector(config) as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1
    except (DatabaseError, InterfaceError, OperationalError, ProgrammingError):
        # Errors raised when the connection is not possible
        return False
