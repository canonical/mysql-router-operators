# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Watchdog loop: restart MySQL Router if RW listener is stuck closed (BUG#39215253).

Standard library only: run as a plain script by /usr/bin/python3.
"""

import argparse
import logging
import logging.handlers
import pathlib
import subprocess
import time

try:
    from . import detect
except ImportError:  # Run as a script
    import detect  # type: ignore[no-redef]

POLL_INTERVAL_S = 10.0
STUCK_THRESHOLD_S = 60.0
EVENT_NAME = "rw_listener_stuck"

logger = logging.getLogger("rw-listener-watchdog")


class StuckTracker:
    """Track how long each route has been continuously STUCK."""

    def __init__(self, threshold_s: float = STUCK_THRESHOLD_S):
        self._threshold_s = threshold_s
        self._since: dict[str, float] = {}

    def update(self, probe: "detect.RouteProbe", now: float) -> bool:
        """Return True if route has been STUCK for at least the threshold (and reset timer)."""
        if probe.result is not detect.ProbeResult.STUCK:
            self._since.pop(probe.route, None)
            return False
        since = self._since.setdefault(probe.route, now)
        if now - since >= self._threshold_s:
            del self._since[probe.route]
            return True
        return False

    def reset(self, route: str) -> None:
        """Forget stuck state of route."""
        self._since.pop(route, None)


def dispatch(unit_name: str, charm_dir: pathlib.Path) -> None:
    """Dispatch custom Juju event to restart router."""
    subprocess.run(
        [
            "/usr/bin/juju-exec",
            "-u",
            unit_name,
            f"JUJU_DISPATCH_PATH=hooks/{EVENT_NAME}",
            f"{charm_dir}/dispatch",
        ],
        check=False,
        timeout=900,
    )


def run_once(
    tracker: StuckTracker,
    *,
    unit_name: str,
    charm_dir: pathlib.Path,
    cred_file: pathlib.Path,
    now: float,
) -> bool:
    """Probe all routes; dispatch (at most once) if any is due. Return whether dispatched."""
    credentials = detect.read_credentials(cred_file)
    if credentials is None:
        return False
    username, password = credentials
    due = False
    for route in detect.ROUTES:
        try:
            probe = detect.probe_route(route, username=username, password=password)
        except Exception:
            logger.exception("Probe of route %s raised; treating as unknown", route)
            tracker.reset(route)
            continue
        if probe.result is detect.ProbeResult.STUCK:
            logger.info("Route %s stuck: %s", route, probe.detail)
        if tracker.update(probe, now):
            due = True
    if due:
        logger.warning("Dispatching %s for %s", EVENT_NAME, unit_name)
        dispatch(unit_name, charm_dir)
    return due


def _setup_logging(charm_dir: pathlib.Path) -> None:
    try:
        handler = logging.handlers.RotatingFileHandler(
            charm_dir.parent / "rw-listener-watchdog.log", maxBytes=1_000_000, backupCount=1
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def main() -> None:
    """Run the watchdog loop until the charm directory disappears."""
    parser = argparse.ArgumentParser(description="MySQL Router RW listener watchdog")
    parser.add_argument("unit_name")
    parser.add_argument("charm_dir", type=pathlib.Path)
    parser.add_argument("cred_file", type=pathlib.Path)
    args = parser.parse_args()
    _setup_logging(args.charm_dir)
    tracker = StuckTracker()
    while args.charm_dir.exists():
        try:
            run_once(
                tracker,
                unit_name=args.unit_name,
                charm_dir=args.charm_dir,
                cred_file=args.cred_file,
                now=time.monotonic(),
            )
        except Exception:
            logger.exception("Watchdog iteration failed")
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
