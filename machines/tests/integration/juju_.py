# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import importlib.metadata

# libjuju version != juju agent version, but the major version should be identical—which is good
_libjuju_version = importlib.metadata.version("juju")
is_3_or_higher = int(_libjuju_version.split(".")[0]) >= 3
