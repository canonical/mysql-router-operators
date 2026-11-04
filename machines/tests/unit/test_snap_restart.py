# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock

import snap


def test_restart_mysql_router_service():
    fake_snap = unittest.mock.MagicMock()
    snap_ = snap.Snap.__new__(snap.Snap)
    with unittest.mock.patch.object(
        snap.Snap, "_snap", new_callable=unittest.mock.PropertyMock, return_value=fake_snap
    ):
        snap_.restart_mysql_router_service()
    fake_snap.restart.assert_called_once_with(["mysqlrouter-service"])
