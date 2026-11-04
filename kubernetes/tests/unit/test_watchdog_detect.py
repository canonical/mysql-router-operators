# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from common.watchdog import detect
from common.watchdog.detect import ProbeResult

HOST = "mysql-0.mysql-endpoints.openstack.svc.cluster.local."
DEST_BODY = json.dumps({"items": [{"address": HOST, "port": 3306}]}).encode()
NOT_ALIVE = b'{"isAlive": false}'
URLOPEN = "common.watchdog.detect.urllib.request.urlopen"
CONNECT = "common.watchdog.detect.socket.create_connection"


def _response(body: bytes):
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


def _http_error(code: int, body: bytes = b""):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


def _urlopen(destinations, health):
    """Build a urlopen side effect; each arg is bytes (200 body) or an exception."""

    def side_effect(request, **kwargs):
        outcome = destinations if request.full_url.endswith("/destinations") else health
        if isinstance(outcome, BaseException):
            raise outcome
        return _response(outcome)

    return side_effect


def _probe():
    return detect.probe_route("bootstrap_rw", username="monitoring", password="pw")


def test_stuck_when_destinations_and_health_500_not_alive_and_tcp_ok():
    health = _http_error(500, NOT_ALIVE)
    with patch(URLOPEN, side_effect=_urlopen(DEST_BODY, health)), patch(CONNECT):
        probe = _probe()
    assert probe.result is ProbeResult.STUCK
    assert probe.destinations == [(HOST, 3306)]


def test_ok_when_alive():
    with patch(URLOPEN, side_effect=_urlopen(DEST_BODY, b'{"isAlive": true}')), patch(CONNECT):
        assert _probe().result is ProbeResult.OK


def test_ok_when_no_destinations():
    health = _http_error(500, NOT_ALIVE)
    with patch(URLOPEN, side_effect=_urlopen(b'{"items": []}', health)), patch(CONNECT):
        assert _probe().result is ProbeResult.OK


def test_ok_when_primary_tcp_unreachable():
    health = _http_error(500, NOT_ALIVE)
    with (
        patch(URLOPEN, side_effect=_urlopen(DEST_BODY, health)),
        patch(CONNECT, side_effect=OSError("refused")),
    ):
        assert _probe().result is ProbeResult.OK


def test_unknown_on_401():
    with patch(URLOPEN, side_effect=_urlopen(_http_error(401), _http_error(401))):
        assert _probe().result is ProbeResult.UNKNOWN


def test_unknown_on_connection_refused():
    error = urllib.error.URLError(ConnectionRefusedError())
    with patch(URLOPEN, side_effect=_urlopen(error, error)):
        assert _probe().result is ProbeResult.UNKNOWN


def test_unknown_on_500_without_json():
    health = _http_error(500, b"<html>oops</html>")
    with patch(URLOPEN, side_effect=_urlopen(DEST_BODY, health)), patch(CONNECT):
        assert _probe().result is ProbeResult.UNKNOWN


def test_unknown_on_bad_destinations_json():
    health = _http_error(500, NOT_ALIVE)
    with patch(URLOPEN, side_effect=_urlopen(b"not json", health)), patch(CONNECT):
        assert _probe().result is ProbeResult.UNKNOWN


def test_request_uses_basic_auth_and_paths():
    requests = []

    def side_effect(request, **kwargs):
        requests.append(request)
        if request.full_url.endswith("/destinations"):
            return _response(DEST_BODY)
        return _response(b'{"isAlive": true}')

    with patch(URLOPEN, side_effect=side_effect), patch(CONNECT):
        _probe()
    base = "https://127.0.0.1:8443/api/20190715/routes/bootstrap_rw"
    urls = [request.full_url for request in requests]
    assert urls[0] == f"{base}/destinations"
    assert f"{base}/health" in urls
    expected = "Basic " + base64.b64encode(b"monitoring:pw").decode()
    assert all(request.get_header("Authorization") == expected for request in requests)


def test_read_credentials(tmp_path):
    path = tmp_path / "cred"
    path.write_text("monitoring:a:b\n")
    assert detect.read_credentials(path) == ("monitoring", "a:b")
    assert detect.read_credentials(tmp_path / "missing") is None
    path.write_text("nocolon")
    assert detect.read_credentials(path) is None


def _destination_body(address, port):
    return json.dumps({"items": [{"address": address, "port": port}]}).encode()


def test_unknown_on_empty_destination_address():
    health = _http_error(500, NOT_ALIVE)
    for address in ("", "   "):
        with patch(URLOPEN, side_effect=_urlopen(_destination_body(address, 3306), health)):
            with patch(CONNECT) as connect:
                assert _probe().result is ProbeResult.UNKNOWN
        connect.assert_not_called()


def test_unknown_on_out_of_range_destination_port():
    health = _http_error(500, NOT_ALIVE)
    for port in (0, 70000):
        with patch(URLOPEN, side_effect=_urlopen(_destination_body(HOST, port), health)):
            with patch(CONNECT) as connect:
                assert _probe().result is ProbeResult.UNKNOWN
        connect.assert_not_called()
