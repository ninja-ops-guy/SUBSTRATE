#!/usr/bin/env python3
"""SUBSTRATE R720 hardware qualification harness.

The harness records PASS/FAIL/SKIP/ERROR explicitly.  A run is only qualified
when every selected required test passes; SKIP never upgrades to PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional


@dataclass
class HardwareProfile:
    model: str
    cpu_model: str
    num_sockets: int
    cores_per_socket: int
    threads_per_core: int
    total_memory_gb: float
    memory_type: str
    memory_speed_mhz: int
    numa_nodes: int
    numa_topology: List[Dict]
    storage_devices: List[Dict]
    network_interfaces: List[Dict]


@dataclass
class QualificationResult:
    test_id: str
    category: str
    status: str
    message: str = ""
    details: Dict = field(default_factory=dict)
    duration_ms: float = 0.0


class HardwareProbe:
    @staticmethod
    def _run(cmd: List[str]) -> str:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return cp.stdout if cp.returncode == 0 else ""

    @staticmethod
    def _read(path: str, default: str = "") -> str:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return default

    def probe(self) -> HardwareProfile:
        lscpu = self._run(["lscpu", "-J"])
        values: Dict[str, str] = {}
        try:
            for row in json.loads(lscpu).get("lscpu", []):
                values[row["field"].rstrip(":")] = row.get("data", "")
        except (ValueError, TypeError, KeyError):
            pass

        mem_kb = 0
        for line in self._read("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
                break

        topology: List[Dict] = []
        for node in sorted(Path("/sys/devices/system/node").glob("node[0-9]*")):
            try:
                node_id = int(node.name[4:])
            except ValueError:
                continue
            mem = 0
            try:
                for line in (node / "meminfo").read_text().splitlines():
                    if "MemTotal" in line:
                        mem = int(line.split()[-2])
                        break
            except OSError:
                pass
            cpus = self._expand_cpu_list(self._read(str(node / "cpulist")))
            topology.append({"node_id": node_id, "memory_kb": mem, "cpus": cpus})

        model = self._read("/sys/class/dmi/id/product_name", "unknown")
        cpu_model = values.get("Model name", "unknown")
        sockets = int(values.get("Socket(s)", "0") or 0)
        cores = int(values.get("Core(s) per socket", "0") or 0)
        threads = int(values.get("Thread(s) per core", "0") or 0)
        numa_nodes = int(values.get("NUMA node(s)", str(len(topology))) or len(topology))

        storage: List[Dict] = []
        if shutil.which("lsblk"):
            cp = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,FSTYPE,MODEL"],
                                capture_output=True, text=True, timeout=10)
            if cp.returncode == 0:
                try:
                    storage = json.loads(cp.stdout).get("blockdevices", [])
                except ValueError:
                    pass

        net = []
        for iface in Path("/sys/class/net").glob("*"):
            net.append({"name": iface.name, "operstate": self._read(str(iface / "operstate"), "unknown")})

        return HardwareProfile(
            model=model, cpu_model=cpu_model, num_sockets=sockets,
            cores_per_socket=cores, threads_per_core=threads,
            total_memory_gb=mem_kb / 1024 / 1024, memory_type="unknown",
            memory_speed_mhz=0, numa_nodes=numa_nodes,
            numa_topology=topology, storage_devices=storage, network_interfaces=net,
        )

    @staticmethod
    def _expand_cpu_list(value: str) -> List[int]:
        cpus: List[int] = []
        for part in filter(None, value.split(",")):
            if "-" in part:
                a, b = part.split("-", 1)
                cpus.extend(range(int(a), int(b) + 1))
            else:
                cpus.append(int(part))
        return cpus


class QualificationTest:
    VALID = {"PASS", "FAIL", "SKIP", "ERROR"}

    def __init__(self, hw: HardwareProfile):
        self.hw = hw

    def run(self, test_id: str, category: str, func: Callable[[], Dict]) -> QualificationResult:
        started = time.monotonic()
        try:
            result = func() or {}
            status = str(result.pop("status", "PASS")).upper()
            if status not in self.VALID:
                status = "ERROR"
            message = str(result.pop("message", ""))
            details = result
        except Exception as exc:
            status, message, details = "ERROR", f"{type(exc).__name__}: {exc}", {}
        return QualificationResult(test_id, category, status, message, details,
                                   (time.monotonic() - started) * 1000)


class NUMACapability(QualificationTest):
    def tests(self) -> List[QualificationResult]:
        return [
            self.run("topology_consistency", "NUMA", self.topology_consistency),
            self.run("memory_distribution", "NUMA", self.memory_distribution),
            self.run("cross_node_latency", "NUMA", self.cross_node_latency),
        ]

    def topology_consistency(self) -> Dict:
        count = len(self.hw.numa_topology)
        ok = self.hw.numa_nodes == count and count >= 2
        return {"status": "PASS" if ok else "FAIL", "reported_nodes": self.hw.numa_nodes, "sysfs_nodes": count}

    def memory_distribution(self) -> Dict:
        memory = [n.get("memory_kb", 0) for n in self.hw.numa_topology]
        if len(memory) < 2 or not all(memory):
            return {"status": "FAIL", "message": "NUMA memory totals unavailable", "nodes_kb": memory}
        ratio = max(memory) / min(memory)
        return {"status": "PASS" if ratio <= 1.25 else "FAIL", "max_to_min_ratio": ratio, "nodes_kb": memory}

    def cross_node_latency(self) -> Dict:
        if not shutil.which("numactl"):
            return {"status": "SKIP", "message": "numactl not installed"}
        cp = subprocess.run(["numactl", "--hardware"], capture_output=True, text=True, timeout=10)
        if cp.returncode != 0 or "node distances:" not in cp.stdout:
            return {"status": "FAIL", "message": "NUMA distance matrix unavailable"}
        lines = cp.stdout.split("node distances:", 1)[1].strip().splitlines()
        return {"status": "PASS", "distance_matrix": lines}


class MemoryCapability(QualificationTest):
    def tests(self) -> List[QualificationResult]:
        return [
            self.run("hugepage_support", "Memory", self.hugepage_support),
            self.run("zram_availability", "Memory", self.zram_availability),
            self.run("memory_bandwidth", "Memory", self.memory_bandwidth),
        ]

    def hugepage_support(self) -> Dict:
        root = Path("/sys/kernel/mm/hugepages")
        sizes = sorted(p.name for p in root.glob("hugepages-*kB")) if root.exists() else []
        one_gib = root / "hugepages-1048576kB"
        two_mib = root / "hugepages-2048kB"
        ok = two_mib.exists() or one_gib.exists()
        return {"status": "PASS" if ok else "FAIL", "sizes": sizes,
                "supports_2MiB": two_mib.exists(), "supports_1GiB": one_gib.exists()}

    def zram_availability(self) -> Dict:
        present = bool(list(Path("/sys/block").glob("zram*"))) or Path("/sys/module/zram").exists()
        return {"status": "PASS" if present else "SKIP", "message": "zram not currently available" if not present else ""}

    def memory_bandwidth(self) -> Dict:
        if shutil.which("sysbench"):
            cp = subprocess.run(["sysbench", "memory", "--memory-block-size=1M", "--memory-total-size=1G", "run"],
                                capture_output=True, text=True, timeout=30)
            return {"status": "PASS" if cp.returncode == 0 else "FAIL", "output_tail": cp.stdout[-2000:]}
        return {"status": "SKIP", "message": "sysbench not installed; bandwidth evidence not collected"}


class StorageCapability(QualificationTest):
    def tests(self) -> List[QualificationResult]:
        return [self.run("btrfs_support", "Storage", self.btrfs_support),
                self.run("raid_configuration", "Storage", self.raid_configuration)]

    def btrfs_support(self) -> Dict:
        fs = Path("/proc/filesystems").read_text() if Path("/proc/filesystems").exists() else ""
        module = Path("/sys/module/btrfs").exists()
        available = "btrfs" in fs or module or bool(shutil.which("btrfs"))
        return {"status": "PASS" if available else "FAIL", "kernel_module": module}

    def raid_configuration(self) -> Dict:
        if not shutil.which("lspci"):
            return {"status": "SKIP", "message": "lspci not installed"}
        cp = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
        matches = [line for line in cp.stdout.splitlines() if "raid" in line.lower() or "perc" in line.lower()]
        mdstat = Path("/proc/mdstat").read_text() if Path("/proc/mdstat").exists() else ""
        return {"status": "PASS" if matches or "active" in mdstat else "SKIP",
                "controllers": matches, "mdstat": mdstat[:2000]}


class CgroupCapability(QualificationTest):
    def tests(self) -> List[QualificationResult]:
        return [self.run("cgroup_v2_mounted", "Cgroup", self.cgroup_v2_mounted),
                self.run("memory_controller", "Cgroup", self.memory_controller),
                self.run("freeze_thaw", "Cgroup", self.freeze_thaw)]

    def cgroup_v2_mounted(self) -> Dict:
        controllers = Path("/sys/fs/cgroup/cgroup.controllers")
        return {"status": "PASS" if controllers.exists() else "FAIL", "path": str(controllers)}

    def memory_controller(self) -> Dict:
        path = Path("/sys/fs/cgroup/cgroup.controllers")
        if not path.exists():
            return {"status": "FAIL", "message": "cgroup v2 unavailable"}
        controllers = path.read_text().split()
        return {"status": "PASS" if "memory" in controllers else "FAIL", "controllers": controllers}

    def freeze_thaw(self) -> Dict:
        if os.geteuid() != 0:
            return {"status": "SKIP", "message": "root required for temporary cgroup freeze/thaw"}
        root = Path("/sys/fs/cgroup")
        test = root / f"substrate-freeze-test-{os.getpid()}"
        try:
            test.mkdir()
            freeze = test / "cgroup.freeze"
            if not freeze.exists():
                return {"status": "FAIL", "message": "cgroup.freeze unavailable"}
            freeze.write_text("1")
            frozen = freeze.read_text().strip() == "1"
            freeze.write_text("0")
            thawed = freeze.read_text().strip() == "0"
            return {"status": "PASS" if frozen and thawed else "FAIL", "frozen": frozen, "thawed": thawed}
        finally:
            try:
                (test / "cgroup.freeze").write_text("0")
            except OSError:
                pass
            try:
                test.rmdir()
            except OSError:
                pass


class PSICapability(QualificationTest):
    def tests(self) -> List[QualificationResult]:
        return [self.run("psi_available", "PSI", self.test_psi_available)]

    def test_psi_available(self) -> Dict:
        data = {name: {"available": Path(f"/proc/pressure/{name}").exists()} for name in ("memory", "cpu", "io")}
        return {"status": "PASS" if all(v["available"] for v in data.values()) else "FAIL", **data}


class QualificationHarness:
    SUITES = {
        "NUMA": NUMACapability,
        "Memory": MemoryCapability,
        "Storage": StorageCapability,
        "Cgroup": CgroupCapability,
        "PSI": PSICapability,
    }

    def __init__(self, output_dir: str = "./results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.probe = HardwareProbe()
        self.hw_profile: Optional[HardwareProfile] = None
        self.all_results: List[QualificationResult] = []

    def run_full_qualification(self, category: Optional[str] = None) -> Dict:
        if category is not None and category not in self.SUITES:
            raise ValueError(f"Unknown qualification category: {category}")
        self.hw_profile = self.probe.probe()
        self.all_results = []
        selected = [category] if category else list(self.SUITES)
        for name in selected:
            self.all_results.extend(self.SUITES[name](self.hw_profile).tests())
        report = self._generate_report()
        self._write_report(report)
        return report

    def _generate_report(self) -> Dict:
        counts = {status: sum(r.status == status for r in self.all_results) for status in ("PASS", "FAIL", "SKIP", "ERROR")}
        total = len(self.all_results)
        summary = {
            "total_tests": total,
            "passed": counts["PASS"],
            "failed": counts["FAIL"],
            "skipped": counts["SKIP"],
            "errors": counts["ERROR"],
            "qualified": total > 0 and counts["PASS"] == total,
        }
        return {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "hardware": asdict(self.hw_profile) if self.hw_profile else None,
            "summary": summary,
            "results": [asdict(r) for r in self.all_results],
        }

    def _write_report(self, report: Dict) -> None:
        stamp = int(time.time())
        (self.output_dir / f"qualification-{stamp}.json").write_text(json.dumps(report, indent=2))
        lines = ["# SUBSTRATE Hardware Qualification Summary", "", f"Qualified: **{report['summary']['qualified']}**", ""]
        for r in report["results"]:
            lines.append(f"- {r['status']}: {r['category']} / {r['test_id']} — {r['message']}")
        (self.output_dir / f"qualification-{stamp}.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description="SUBSTRATE Hardware Qualification Harness")
    p.add_argument("--output", "-o", default="./results")
    p.add_argument("--category", choices=list(QualificationHarness.SUITES))
    args = p.parse_args()
    report = QualificationHarness(args.output).run_full_qualification(args.category)
    print(json.dumps(report, indent=2))
    return 0 if report["summary"]["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
