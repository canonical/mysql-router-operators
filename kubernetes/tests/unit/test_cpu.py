# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import pathlib

import common.cpu
import pytest

_STATUS_TEMPLATE = "Name:\tcat\nCpus_allowed:\tff\nCpus_allowed_list:\t{}\nMems_allowed_list:\t0\n"


def _reader(files: dict[str, str]):
    return files.get


@pytest.mark.parametrize(
    "text,count",
    [
        ("0", 1),
        ("0-3", 4),
        ("0-3,8,10-11\n", 7),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_cpu_list(text, count):
    assert common.cpu.parse_cpu_list(text) == count


@pytest.mark.parametrize(
    "text,count",
    [
        ("200000 100000\n", 2),
        ("50000 100000", 1),
        ("150000 100000", 2),
        ("max 100000", None),
        ("null", None),
        ("1 0", None),
    ],
)
def test_parse_cpu_max(text, count):
    assert common.cpu.parse_cpu_max(text) == count


@pytest.mark.parametrize(
    "quota,period,count",
    [("200000", "100000", 2), ("-1", "100000", None), ("null", "null", None)],
)
def test_parse_cfs_quota(quota, period, count):
    assert common.cpu.parse_cfs_quota(quota, period) == count


@pytest.mark.parametrize(
    "text,path",
    [
        ("0::/\n", "/"),
        (
            "0::/kubepods.slice/pod1/cri-containerd-abc.scope\n",
            "/kubepods.slice/pod1/cri-containerd-abc.scope",
        ),
        ("12:cpu,cpuacct:/docker/abc\n0::/\n", "/"),
        ("4:cpu,cpuacct:/docker/abc\n", None),
        ("null", None),
    ],
)
def test_parse_cgroup_v2_path(text, path):
    expected = pathlib.PurePosixPath(path) if path else None
    assert common.cpu.parse_cgroup_v2_path(text) == expected


def test_kubernetes_cpu_limit():
    """Container with private cgroup namespace & `resources.limits.cpu: 2` on a 64 CPU node"""
    read = _reader({
        "/proc/self/status": _STATUS_TEMPLATE.format("0-63"),
        "/proc/self/cgroup": "0::/\n",
        "/sys/fs/cgroup/cpu.max": "200000 100000\n",
    })
    assert common.cpu.effective_cpu_count(read) == 2


def test_kubernetes_no_cpu_limit():
    read = _reader({
        "/proc/self/status": _STATUS_TEMPLATE.format("0-63"),
        "/proc/self/cgroup": "0::/\n",
        "/sys/fs/cgroup/cpu.max": "max 100000\n",
    })
    assert common.cpu.effective_cpu_count(read) == 64


def test_cpuset_pinning():
    """Kubernetes static CPU manager policy or LXD `limits.cpu`"""
    read = _reader({
        "/proc/self/status": _STATUS_TEMPLATE.format("4-7"),
        "/proc/self/cgroup": "0::/\n",
        "/sys/fs/cgroup/cpu.max": "max 100000\n",
        "/sys/fs/cgroup/cpuset.cpus.effective": "4-7\n",
    })
    assert common.cpu.effective_cpu_count(read) == 4


def test_tightest_ancestor_limit_wins():
    """Host cgroup namespace: walk up from own cgroup"""
    read = _reader({
        "/proc/self/status": _STATUS_TEMPLATE.format("0-63"),
        "/proc/self/cgroup": "0::/kubepods.slice/pod1/container1\n",
        "/sys/fs/cgroup/kubepods.slice/pod1/container1/cpu.max": "800000 100000",
        "/sys/fs/cgroup/kubepods.slice/pod1/cpu.max": "300000 100000",
        "/sys/fs/cgroup/kubepods.slice/cpu.max": "max 100000",
    })
    assert common.cpu.effective_cpu_count(read) == 3


def test_explicit_cgroup_path_with_stopped_service():
    """Machine charm: snap service cgroup does not exist while the service is stopped"""
    read = _reader({
        "/proc/self/status": _STATUS_TEMPLATE.format("0-15"),
        "/sys/fs/cgroup/system.slice/cpu.max": "max 100000",
        "/sys/fs/cgroup/cpu.max": "400000 100000",
    })
    cgroup_path = pathlib.PurePosixPath(
        "/system.slice/snap.charmed-mysql.mysqlrouter-service.service"
    )
    assert common.cpu.effective_cpu_count(read, cgroup_path=cgroup_path) == 4


def test_cgroup_v1():
    read = _reader({
        "/proc/self/status": _STATUS_TEMPLATE.format("0-63"),
        "/proc/self/cgroup": "4:cpu,cpuacct:/kubepods/pod1/abc\n",
        "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us": "150000\n",
        "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us": "100000\n",
    })
    assert common.cpu.effective_cpu_count(read) == 2


def test_unknown():
    assert common.cpu.effective_cpu_count(_reader({})) is None
    assert common.cpu.effective_cpu_count(lambda _: "null") is None
