# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess

architecture = subprocess.run(
    ["dpkg", "--print-architecture"],
    capture_output=True,
    check=True,
    text=True,
).stdout.strip()
