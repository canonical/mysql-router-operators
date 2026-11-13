# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Effective CPU count available to the MySQL Router process

MySQL Router's `[io] threads=0` default spawns one IO thread per host CPU, ignoring cgroup CPU
quotas (e.g. Kubernetes `resources.limits.cpu`).
"""

import logging
import math
import pathlib
import typing

logger = logging.getLogger(__name__)

CGROUP_ROOT = pathlib.PurePosixPath("/sys/fs/cgroup")

ReadFile = typing.Callable[[str], str | None]
"""Reads a file as seen by the workload; returns `None` if it cannot be read"""


def _min(*values: int | None) -> int | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def parse_cpu_list(text: str) -> int | None:
    """Count CPUs in a cpu list (e.g. "0-3,8,10-11" -> 7)"""
    count = 0
    try:
        for part in text.strip().split(","):
            if not part:
                continue
            start, _, end = part.partition("-")
            count += int(end or start) - int(start) + 1
    except ValueError:
        return None
    return count or None


def parse_cpus_allowed(proc_status: str) -> int | None:
    """Count CPUs in `Cpus_allowed_list` from `/proc/<pid>/status` (CPU affinity)"""
    for line in proc_status.splitlines():
        key, _, value = line.partition(":")
        if key == "Cpus_allowed_list":
            return parse_cpu_list(value)
    return None


def parse_cgroup_v2_path(proc_cgroup: str) -> pathlib.PurePosixPath | None:
    """Parse cgroup v2 path from `/proc/<pid>/cgroup` (e.g. "0::/kubepods.slice/...")"""
    for line in proc_cgroup.splitlines():
        hierarchy, _, path = line.partition("::")
        if hierarchy == "0" and path.startswith("/"):
            return pathlib.PurePosixPath(path)
    return None


def parse_cpu_max(cpu_max: str) -> int | None:
    """Parse cgroup v2 `cpu.max` (e.g. "200000 100000" -> 2; "max 100000" -> None)"""
    try:
        quota, period = cpu_max.split()
        if quota == "max":
            return None
        return max(1, math.ceil(int(quota) / int(period)))
    except (ValueError, ZeroDivisionError):
        return None


def parse_cfs_quota(quota: str, period: str) -> int | None:
    """Parse cgroup v1 `cpu.cfs_quota_us` & `cpu.cfs_period_us` (quota "-1" -> None)"""
    try:
        quota_us = int(quota)
        if quota_us < 0:
            return None
        return max(1, math.ceil(quota_us / int(period)))
    except (ValueError, ZeroDivisionError):
        return None


def _cgroup_v2_limit(read: ReadFile, cgroup_path: pathlib.PurePosixPath) -> tuple[int | None, bool]:
    """Tightest cgroup v2 CPU limit from `cgroup_path` up to the (namespace) root

    Ancestors that do not exist (e.g. cgroup of a stopped systemd service) are skipped. Ancestors
    above the cgroup namespace root are not visible.

    Returns:
        (limit, whether any `cpu.max` file was readable)
    """
    limit = None
    found_cpu_max = False
    for path in (cgroup_path, *cgroup_path.parents):
        directory = CGROUP_ROOT / path.relative_to("/")
        if (cpu_max := read(str(directory / "cpu.max"))) is not None:
            found_cpu_max = True
            limit = _min(limit, parse_cpu_max(cpu_max))
        if (cpuset := read(str(directory / "cpuset.cpus.effective"))) is not None:
            limit = _min(limit, parse_cpu_list(cpuset))
    return limit, found_cpu_max


def _cgroup_v1_limit(read: ReadFile) -> int | None:
    """cgroup v1 CPU quota of the cgroup mounted at the container's cgroup root"""
    for controller in ("cpu,cpuacct", "cpu"):
        directory = CGROUP_ROOT / controller
        quota = read(str(directory / "cpu.cfs_quota_us"))
        period = read(str(directory / "cpu.cfs_period_us"))
        if quota is not None and period is not None:
            return parse_cfs_quota(quota, period)
    return None


def effective_cpu_count(
    read: ReadFile, *, cgroup_path: pathlib.PurePosixPath | None = None
) -> int | None:
    """Number of CPUs the workload can use: min(CPU affinity, cgroup cpuset, cgroup CPU quota)

    Args:
        read: Reads a file from the workload's point of view. `/proc/self/*` must describe a
            process that shares the workload's CPU affinity & cgroup
        cgroup_path: cgroup v2 path of the workload. If `None`, read from `/proc/self/cgroup`

    Returns:
        CPU count, or `None` if it could not be determined
    """
    affinity = None
    if (status := read("/proc/self/status")) is not None:
        affinity = parse_cpus_allowed(status)

    found_cpu_max = False
    cgroup_limit = None
    if cgroup_path is None and (proc_cgroup := read("/proc/self/cgroup")) is not None:
        cgroup_path = parse_cgroup_v2_path(proc_cgroup)
    if cgroup_path is not None:
        cgroup_limit, found_cpu_max = _cgroup_v2_limit(read, cgroup_path)
    if not found_cpu_max:
        cgroup_limit = _min(cgroup_limit, _cgroup_v1_limit(read))

    count = _min(affinity, cgroup_limit)
    logger.debug(f"Effective CPU count {count=} {affinity=} {cgroup_limit=} {cgroup_path=}")
    return count
