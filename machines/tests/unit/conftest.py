# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest


@pytest.fixture(autouse=True)
def patch_snap_name(monkeypatch):
    # `snap._Path.__init__` calls `charm_refresh.snap_name()`, which would otherwise
    # read `refresh_versions.toml` (unparsable in the unit env). The full set of
    # patches used by the scenario tests lives in common/tests/unit/machines/conftest.py.
    monkeypatch.setattr("charm_refresh.snap_name", lambda: "charmed-mysql")
