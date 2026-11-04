# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Detect MySQL Router RW listener stuck closed (MySQL Router BUG#39215253).

Standard library only: also run by /usr/bin/python3 as a sibling module.
"""

import base64
import dataclasses
import enum
import json
import pathlib
import socket
import ssl
import urllib.error
import urllib.request

ROUTES = ("bootstrap_rw", "bootstrap_x_rw")
_API = "api/20190715"


class ProbeResult(enum.Enum):
    """Outcome of probing one route."""

    STUCK = "stuck"
    OK = "ok"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class RouteProbe:
    """Result of probing one route."""

    route: str
    result: ProbeResult
    destinations: list[tuple[str, int]]
    detail: str


def read_credentials(path: pathlib.Path) -> tuple[str, str] | None:
    """Read `username:password` from file; `None` if missing, unreadable or malformed."""
    try:
        content = pathlib.Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return None
    content = content.rstrip("\n")
    username, separator, password = content.partition(":")
    if not separator or not username:
        return None
    return username, password


def _get_json(url: str, auth: str, timeout: float) -> tuple[int, dict]:
    """GET url; return (status, parsed JSON body). HTTP 500 is returned, not raised.

    Raises on any other failure (including non-JSON body).
    """
    request = urllib.request.Request(url, headers={"Authorization": auth})
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status, body = 200, response.read()
    except urllib.error.HTTPError as exception:
        if exception.code != 500:
            raise
        status, body = 500, exception.read()
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("response body is not a JSON object")
    return status, parsed


def probe_route(
    route: str,
    *,
    username: str,
    password: str,
    rest_host: str = "127.0.0.1",
    rest_port: int = 8443,
    timeout: float = 3.0,
    tcp_timeout: float = 2.0,
) -> RouteProbe:
    """Probe one route. STUCK only if listener is dead while every destination is reachable."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    auth = f"Basic {token}"
    base = f"https://{rest_host}:{rest_port}/{_API}/routes/{route}"

    def unknown(detail: str, destinations=()) -> RouteProbe:
        return RouteProbe(route, ProbeResult.UNKNOWN, list(destinations), detail)

    try:
        status, body = _get_json(f"{base}/destinations", auth, timeout)
        if status != 200:
            return unknown(f"destinations returned HTTP {status}")
        destinations = [(str(item["address"]), int(item["port"])) for item in body["items"]]
        for address, port in destinations:
            # An empty host resolves to localhost and could fake reachability
            if not address.strip() or not 1 <= port <= 65535:
                return unknown(f"malformed destination {address!r}:{port}")
    except urllib.error.HTTPError as exception:
        return unknown(f"destinations returned HTTP {exception.code}")
    except Exception as exception:
        return unknown(f"destinations request failed: {exception!r}")

    if not destinations:
        return RouteProbe(route, ProbeResult.OK, destinations, "no destinations")

    try:
        status, body = _get_json(f"{base}/health", auth, timeout)
    except urllib.error.HTTPError as exception:
        return unknown(f"health returned HTTP {exception.code}", destinations)
    except Exception as exception:
        return unknown(f"health request failed: {exception!r}", destinations)

    is_alive = body.get("isAlive")
    if status == 200:
        if is_alive is True:
            return RouteProbe(route, ProbeResult.OK, destinations, "listener alive")
        return unknown(f"health HTTP 200 with unexpected body {body!r}", destinations)
    if is_alive is not False:
        return unknown(f"health HTTP 500 with unexpected body {body!r}", destinations)

    for address, port in destinations:
        try:
            with socket.create_connection((address, port), timeout=tcp_timeout):
                pass
        except OSError as exception:
            return RouteProbe(
                route,
                ProbeResult.OK,
                destinations,
                f"listener not alive but destination {address}:{port} unreachable: {exception}",
            )
    return RouteProbe(
        route,
        ProbeResult.STUCK,
        destinations,
        "listener not alive while all destinations reachable",
    )
