# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock

import ops

import rock


def test_restart_mysql_router_service():
    container = unittest.mock.create_autospec(ops.Container, instance=True)
    rock_ = rock.Rock.__new__(rock.Rock)
    rock_._container = container
    rock_.restart_mysql_router_service()
    container.restart.assert_called_once_with("mysql_router")
