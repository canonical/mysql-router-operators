# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Runs logrotate hourly."""

import subprocess
import time


def main():
    """Main watch and dispatch loop.

    Roughly every hour at the top of the hour, execute logrotate. Do not pass
    ``-f``: rotation must be driven by the frequency directive and the content
    of the log file (``notifempty``), not forced on every run.
    """
    # wait till the top of the hour
    time.sleep(3600 - (time.time() % 3600))
    start_time = time.monotonic()

    while True:
        subprocess.run(
            [
                "logrotate",
                "-s",
                "/tmp/logrotate.status",
                "/etc/logrotate.d/flush_mysqlrouter_logs",
            ],
            check=True,
        )

        # wait again till the top of the next hour
        time.sleep(3600.0 - ((time.monotonic() - start_time) % 3600.0))


if __name__ == "__main__":
    main()
