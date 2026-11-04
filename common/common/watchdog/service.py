# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-side manager for the detached RW listener watchdog process."""

import logging
import os
import pathlib
import signal
import subprocess

logger = logging.getLogger(__name__)

MAIN_PATH = pathlib.Path(__file__).parent / "main.py"
_PYTHON = "/usr/bin/python3"


def _proc_cmdline(pid: int) -> str | None:
    """Return the process command line (NUL separators kept) or None if not found."""
    try:
        return pathlib.Path(f"/proc/{pid}/cmdline").read_text()
    except OSError:
        return None


class WatchdogService:
    """Start and stop the RW listener watchdog."""

    def __init__(self, *, agent_dir: pathlib.Path):
        self._agent_dir = pathlib.Path(agent_dir)

    @property
    def pid_file(self) -> pathlib.Path:
        """File holding the watchdog PID."""
        return self._agent_dir / "rw-listener-watchdog.pid"

    @property
    def cred_file(self) -> pathlib.Path:
        """File holding `username:password` for the watchdog."""
        return self._agent_dir / "rw-listener-watchdog.cred"

    def _pid(self) -> int | None:
        try:
            return int(self.pid_file.read_text().strip())
        except (OSError, ValueError):
            return None

    @property
    def running(self) -> bool:
        """Whether the watchdog process is alive."""
        pid = self._pid()
        if pid is None:
            return False
        cmdline = _proc_cmdline(pid)
        return cmdline is not None and "watchdog/main.py" in cmdline

    def _write_cred_file(self, content: str) -> None:
        try:
            if self.cred_file.read_text() == content:
                return
        except OSError:
            pass
        fd = os.open(self.cred_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(content)

    def ensure_running(
        self,
        *,
        unit_name: str,
        charm_dir: pathlib.Path,
        username: str,
        password: str,
    ) -> None:
        """Write credentials and start the watchdog if it is not running."""
        self._write_cred_file(f"{username}:{password}")
        if self.running:
            return
        env = {k: v for k, v in os.environ.items() if k != "JUJU_CONTEXT_ID"}
        process = subprocess.Popen(
            [_PYTHON, str(MAIN_PATH), unit_name, str(charm_dir), str(self.cred_file)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.pid_file.write_text(str(process.pid))
        logger.info(f"Started RW listener watchdog with PID {process.pid}")

    def stop(self) -> None:
        """Stop the watchdog and remove its state files."""
        pid = self._pid()
        if self.running and pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            logger.info(f"Stopped RW listener watchdog with PID {pid}")
        self.pid_file.unlink(missing_ok=True)
        self.cred_file.unlink(missing_ok=True)
