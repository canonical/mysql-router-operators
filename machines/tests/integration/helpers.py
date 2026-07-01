# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import itertools

from .connector import MySQLConnector


def execute_queries_against_unit(
    username: str,
    password: str,
    host: str,
    port: int,
    queries: list[str],
    commit: bool = False,
) -> list:
    """Execute given MySQL queries on a unit.

    Args:
        username: The MySQL username
        password: The MySQL password
        host: The host to connect to in order to execute queries
        port: The port to connect to in order to execute queries
        queries: A list of queries to execute
        commit: A keyword arg indicating whether there are any writes queries

    Returns:
        A list of rows that were potentially queried
    """
    config = {
        "user": username,
        "password": password,
        "host": host,
        "port": port,
        "raise_on_warnings": False,
    }

    with MySQLConnector(config, commit) as cursor:
        for query in queries:
            cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))

    return output
