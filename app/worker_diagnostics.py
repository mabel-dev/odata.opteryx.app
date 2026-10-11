"""TEMPORARY: why do uvicorn workers die under ClickBench q_20?

Every 503 in the 2026-10-11 run was uvicorn reporting "Child process [N] died",
then Cloud Run SIGTERMing the instance ~0.5s later. uvicorn logs that line for
both ways a child can die - the kernel OOM-killed it, or the supervisor killed
it for missing its ping - so the logs cannot tell them apart. This can.

Sources are /proc only. Cloud Run gen2 runs each instance in its own VM and
does not expose the container's memory cgroup at /sys/fs/cgroup (the first
deploy of this module failed its probe on memory.events), so the kernel's own
counters stand in: /proc/vmstat `oom_kill` counts every OOM kill, cgroup-limit
kills included, and /proc/meminfo is the VM's memory.

Each worker runs one daemon thread that:
  - every 0.25s reads /proc/vmstat `oom_kill`, and logs the moment it moves
    (the surviving worker sees its sibling's kill well inside the ~0.5s before
    SIGTERM);
  - every 2s logs a sample: this worker's RSS and high-water mark, the VM's
    MemTotal/MemAvailable, and the engine's own accounting (chunk cache bytes,
    charged memory).

Reading it: `oom_kill` incremented just before a death = memory, and the
samples say who held it. Samples flat and no `oom_kill` = the health check.

Enabled by WORKER_DIAGNOSTICS=1 (set in the Dockerfile). Delete this module,
its call in main.py and the two Dockerfile changes once the cause is known.
"""

import os
import threading
import time

from opteryx.compiled.structures.memory_pool import chunk_cache_stats
from opteryx.compiled.structures.memory_pool import process_memory_charged
from opteryx_shared_services.logging import get_logger

_EVENTS_INTERVAL_S = 0.25
_SAMPLE_EVERY = 8  # events ticks per sample: 8 x 0.25s = 2s

logger = get_logger()


def _read_oom_kill() -> int:
    with open("/proc/vmstat") as f:
        for line in f:
            if line.startswith("oom_kill "):
                return int(line.split()[1])
    raise RuntimeError("/proc/vmstat has no oom_kill counter")


def _read_kib_fields(path: str, fields: tuple) -> dict:
    """`Name:  <n> kB` lines from a /proc file, in bytes; raises if any is absent."""
    found = {}
    with open(path) as f:
        for line in f:
            key = line.split(":", 1)[0]
            if key in fields:
                found[key] = int(line.split()[1]) * 1024
    missing = [field for field in fields if field not in found]
    if missing:
        raise RuntimeError(f"{path} has no {', '.join(missing)}")
    return found


def _sample() -> dict:
    status = _read_kib_fields("/proc/self/status", ("VmRSS", "VmHWM"))
    meminfo = _read_kib_fields("/proc/meminfo", ("MemTotal", "MemAvailable"))
    cache = chunk_cache_stats()
    return {
        "pid": os.getpid(),
        "rss_bytes": status["VmRSS"],
        "rss_peak_bytes": status["VmHWM"],
        "mem_total_bytes": meminfo["MemTotal"],
        "mem_available_bytes": meminfo["MemAvailable"],
        "chunk_cache_bytes": cache["bytes"],
        "chunk_cache_budget_bytes": cache["budget"],
        "engine_charged_bytes": process_memory_charged(),
    }


def _run() -> None:
    last_oom_kill = _read_oom_kill()
    tick = 0
    while True:
        time.sleep(_EVENTS_INTERVAL_S)
        tick += 1
        oom_kill = _read_oom_kill()
        if oom_kill != last_oom_kill:
            logger.warning(
                "worker-diag oom_kill changed",
                extra={"diag": "oom_kill", "oom_kill_before": last_oom_kill, "oom_kill_after": oom_kill, **_sample()},
            )
            last_oom_kill = oom_kill
        if tick % _SAMPLE_EVERY == 0:
            logger.info("worker-diag sample", extra={"diag": "sample", "oom_kill": oom_kill, **_sample()})


def start() -> None:
    """Start the sampler in this worker."""
    logger.info("worker-diag started", extra={"diag": "start", "oom_kill": _read_oom_kill(), **_sample()})
    threading.Thread(target=_run, name="worker-diagnostics", daemon=True).start()


if __name__ == "__main__":
    # Run from the Dockerfile CMD after app.preflight, before the server: a
    # missing source raises here, the container exits non-zero and the revision
    # fails - rather than every worker dying in its lifespan behind an open port.
    with open("/proc/self/cgroup") as f:
        cgroup = f.read().strip()
    print(f"worker-diag sources ok: oom_kill={_read_oom_kill()} cgroup={cgroup!r} sample={_sample()}")
