# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""logrotate cron configuration"""

import logging

import common.container
import common.logrotate

logger = logging.getLogger(__name__)

CHARMED_MYSQL_COMMON_DIRECTORY = "/var/snap/charmed-mysql/common"
ROOT_USER = "root"


class LogRotate(common.logrotate.LogRotate):
    """logrotate cron configuration"""

    _SYSTEM_USER = "snap_daemon"

    def __init__(self, *, container_: common.container.Container):
        super().__init__(container_=container_)
        self._cron_file = self._container.path("/etc/cron.d/flush_mysqlrouter_logs")

    def enable(self) -> None:
        super().enable()

        logger.debug("Adding cron job for logrotate")

        # cron needs the file to be owned by root
        # Run hourly, letting the config's `hourly`/`notifempty` directives
        # decide when rotation actually happens: do not pass `-f` (mr-234).
        self._cron_file.write_text(
            "0 * * * * snap_daemon logrotate -s /tmp/logrotate.status /etc/logrotate.d/flush_mysqlrouter_logs\n\n",
            user=ROOT_USER,
            group=ROOT_USER,
        )

        logger.debug("Added cron job for logrotate")

    def disable(self) -> None:
        logger.debug("Removing cron job for log rotation of mysqlrouter")
        super().disable()
        self._cron_file.unlink(missing_ok=True)
        logger.debug("Removed cron job for log rotation of mysqlrouter")
