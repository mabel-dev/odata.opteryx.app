"""TEMPORARY: why do uvicorn workers die under ClickBench q_20?

Every 503 in the 2026-10-11 run was uvicorn reporting "Child process [N] died",
then Cloud Run SIGTERMing the instance ~0.5s later. uvicorn logs that line for
both ways a child can die - the kernel OOM-killed it, or the supervisor killed
it for missing its ping - so the logs cannot tell them apart. This can.

Each worker runs one daemon thread that:
  - every 0.25s reads the container cgroup's `memory.events`, and logs the
    moment `oom` / `oom_kill` moves (the surviving worker sees its sibling's
    kill well inside the ~0.5s before SIGTERM);
  - every 2s logs a sample: this worker's RSS and high-water mark, the
    container's memory.current against memory.max, and the engine's own
    accounting (chunk cache bytes, charged memory).

Reading it: `oom_kill` incremented just before a death = memory, and the
samples say who held it. Samples flat and no `oom_kill` = the health check.

Enabled by WORKER_DIAGNOSTICS=1 (set in the Dockerfile). Delete this module,
its call in main.py and the Dockerfile ENV once the cause is known.
"""

import os
import threading
import time

from opteryx.compiled.structures.memory_pool import chunk_cache_stats
from opteryx.compiled.structures.memory_pool import process_memory_charged
from opteryx_shared_services.logging import get_logger

_CGROUP = "/sys/fs/cgroup"
_EVENTS_INTERVAL_S = 0.25
_SAMPLE_EVERY = 8  # events ticks per sample: 8 x 0.25s = 2s

logger = get_logger()


def _read_memory_events() -> dict:
    with open(f"{_CGROUP}/memory.events") as f:
        return {key: int(value) for key, value in (line.split() for line in f)}


def _read_cgroup(name: str) -> str:
    with open(f"{_CGROUP}/{name}") as f:
        return f.read().strip()


def _proc_status_bytes() -> dict:
    """VmRSS (current) and VmHWM (peak) for this process, in bytes."""
    found = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, kib, _unit = line.split()
                found[key[:-1]] = int(kib) * 1024
    return found


def _sample() -> dict:
    status = _proc_status_bytes()
    cache = chunk_cache_stats()
    return {
        "pid": os.getpid(),
        "rss_bytes": status["VmRSS"],
        "rss_peak_bytes": status["VmHWM"],
        "cgroup_current_bytes": int(_read_cgroup("memory.current")),
        "cgroup_max": _read_cgroup("memory.max"),
        "chunk_cache_bytes": cache["bytes"],
        "chunk_cache_budget_bytes": cache["budget"],
        "engine_charged_bytes": process_memory_charged(),
    }


def _run() -> None:
    last_events = _read_memory_events()
    tick = 0
    while True:
        time.sleep(_EVENTS_INTERVAL_S)
        tick += 1
        events = _read_memory_events()
        if events != last_events:
            logger.warning(
                "worker-diag memory.events changed",
                extra={"diag": "events", "events_before": last_events, "events_after": events, **_sample()},
            )
            last_events = events
        if tick % _SAMPLE_EVERY == 0:
            logger.info("worker-diag sample", extra={"diag": "sample", "events": events, **_sample()})


def start() -> None:
    """Start the sampler in this worker. Reads every source once first, so a
    container without cgroup v2 or /proc fails the worker's startup loudly
    instead of running blind."""
    logger.info("worker-diag started", extra={"diag": "start", "events": _read_memory_events(), **_sample()})
    threading.Thread(target=_run, name="worker-diagnostics", daemon=True).start()


if __name__ == "__main__":
    # Run from the Dockerfile CMD after app.preflight, before the server: a
    # missing source raises here, the container exits non-zero and the revision
    # fails - rather than every worker dying in its lifespan behind an open port.
    print(f"worker-diag sources ok: events={_read_memory_events()} sample={_sample()}")
