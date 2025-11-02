# disk-status-exporter/main.py

from fastapi import FastAPI, Response
import os
import glob
import re
import shutil
import subprocess
from typing import Dict, Iterable, Optional
import time
import logging
import asyncio

app = FastAPI()

PROBE_ATTEMPTS = max(int(os.getenv("PROBE_ATTEMPTS", "5")), 1)
PROBE_INTERVAL_MS = max(int(os.getenv("PROBE_INTERVAL_MS", "1000")), 0)
MAX_CONCURRENCY = max(int(os.getenv("MAX_CONCURRENCY", "8")), 1)  # NEW

logger = logging.getLogger("disk_status_exporter")
if not logger.handlers:
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    formatter = logging.Formatter(
        fmt="ts=%(asctime)s level=%(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


@app.on_event("startup")
def _startup_log():
    logger.info(
        "disk-status-exporter starting (version=%s)", os.getenv("VERSION", "unknown")
    )
    logger.info(
        "probe settings: PROBE_ATTEMPTS=%d PROBE_INTERVAL_MS=%d MAX_CONCURRENCY=%d",
        PROBE_ATTEMPTS,
        PROBE_INTERVAL_MS,
        MAX_CONCURRENCY,
    )


# Single numeric gauge keeps things simple. Use disk_info{} for metadata joins.
STATE_MAP: Dict[str, int] = {
    "unknown": -1,
    "error": -2,
    "standby": 0,
    "idle": 1,
    "active_or_idle": 2,
    "idle_a": 3,
    "idle_b": 4,
    "idle_c": 5,
    "active": 6,
    "sleep": 7,
}

# seagate ironwolf pro 16TB power specification
ACTIVITY_RANK: Dict[str, int] = {
    "error": -1,  # treat errors as lowest, below unknown
    "unknown": 0,
    "sleep": 1,
    "standby": 2,
    "idle_a": 3,
    "idle_b": 4,
    "idle_c": 5,
    "idle": 6,
    "active_or_idle": 7,
    "active": 8,
}

PREFERRED_ID_PREFIX = ("ata-", "scsi-", "wwn-", "nvme-", "usb-", "virtio-")


def highest_activity_state(a: str, b: str) -> str:
    """Return the state with higher activity according to ACTIVITY_RANK."""
    return a if ACTIVITY_RANK.get(a, 0) >= ACTIVITY_RANK.get(b, 0) else b


def list_block_devices() -> Iterable[str]:
    """
    Enumerate block devices by kname via /sys/block, skipping virtual devices.
    Returns iterable of /dev/<kname> paths.
    """
    sys_block = "/sys/block"
    if not os.path.isdir(sys_block):
        return []

    for kname in os.listdir(sys_block):
        # Skip virtual and mapper/loop devices, and optical drives
        if kname.startswith(("loop", "ram", "fd", "sr")) or kname.startswith(("dm-",)):
            continue
        # Example kname: sda, sdb, nvme0n1, vda, etc.
        dev = f"/dev/{kname}"
        if os.path.exists(dev):
            yield dev


def get_rotational_type(dev: str) -> str:
    """
    Return 'hdd' if rotational==1, 'ssd' if 0, else 'unknown'.
    """
    kname = os.path.basename(dev)
    path = f"/sys/block/{kname}/queue/rotational"
    try:
        with open(path, "r") as f:
            return "hdd" if f.read().strip() == "1" else "ssd"
    except Exception:
        return "unknown"


def is_rotational(dev: str) -> bool:
    return get_rotational_type(dev) == "hdd"


def get_persistent_id(dev: str) -> str:
    """
    Return a stable /dev/disk/by-id/<id> symlink (preferred prefixes first).
    Fall back to the raw /dev/<kname> if no by-id link exists.
    """
    by_id_dir = "/dev/disk/by-id"
    if not os.path.isdir(by_id_dir):
        return dev

    real = os.path.realpath(dev)
    candidates = []
    for path in glob.glob(os.path.join(by_id_dir, "*")):
        try:
            if os.path.realpath(path) == real:
                candidates.append(os.path.basename(path))
        except Exception:
            continue

    if not candidates:
        return dev

    # Prefer human-friendly, stable prefixes
    candidates.sort(
        key=lambda n: (0 if n.startswith(PREFERRED_ID_PREFIX) else 1, len(n), n)
    )
    return f"/dev/disk/by-id/{candidates[0]}"


def is_virtual_device(dev: str) -> bool:
    """
    Heuristics to filter out QEMU/virtual devices:
    - /sys/block/<kname>/device/{vendor,model} contains QEMU or VIRTUAL
    - device_id starts with scsi-0QEMU_, ata-QEMU_, or virtio-
    """
    kname = os.path.basename(dev)
    vendor_path = f"/sys/block/{kname}/device/vendor"
    model_path = f"/sys/block/{kname}/device/model"

    vend = ""
    model = ""
    try:
        with open(vendor_path, "r") as f:
            vend = f.read().strip().upper()
    except Exception:
        pass
    try:
        with open(model_path, "r") as f:
            model = f.read().strip().upper()
    except Exception:
        pass

    if "QEMU" in vend or "QEMU" in model or "VIRTUAL" in vend or "VIRTUAL" in model:
        return True

    base_id = os.path.basename(get_persistent_id(dev))
    if base_id.startswith(("scsi-0QEMU_", "ata-QEMU_", "virtio-")):
        return True

    return False


def get_zpool_device_map() -> Dict[str, str]:
    """
    Optionally map base device -> zpool name by parsing `zpool status -L -P`.
    Returns dict like {"/dev/sdX": "tank"}.
    If zpool is unavailable, returns {} quickly.
    """
    if shutil.which("zpool") is None:
        return {}

    pool_map: Dict[str, str] = {}
    try:
        # -P prints full paths, -L follows symlinks
        result = subprocess.run(
            ["zpool", "status", "-L", "-P"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        current_pool: Optional[str] = None
        in_config = False

        for raw in result.stdout.splitlines():
            line = raw.rstrip()

            if line.strip().startswith("pool:"):
                current_pool = line.split(":", 1)[1].strip()
                in_config = False
                continue
            if line.strip().startswith("config:"):
                in_config = True
                continue
            if not in_config or not current_pool:
                continue

            s = line.strip()
            # Skip headers/virtual vdev labels
            if re.match(r"^(NAME|mirror-|special|logs|spare|cache|raidz|stripe)", s):
                continue

            # Try to capture a real device path, possibly a partition
            # e.g. /dev/sdd1, /dev/disk/by-id/ata-SN123-part1
            m = re.match(r"^(/dev/\S+)", s)
            if not m:
                continue

            devpath = m.group(1)
            # Strip partition suffix for base disk, but keep /dev/disk/by-id paths
            if devpath.startswith("/dev/disk/by-id/"):
                # Try to resolve to real base device for matching
                real = os.path.realpath(devpath)
                base = re.sub(r"p?\d+$", "", real)
                pool_map[base] = current_pool
            else:
                base = re.sub(r"p?\d+$", "", devpath)
                pool_map[base] = current_pool

    except Exception as e:
        logger.error(f"ERR [zpool] skipped (error: {e})")
        return {}

    return pool_map


def smartctl_power_state(dev: str) -> str:
    """
    Use smartctl without waking the drive to infer power state from stdout.
    We don't trust the exit code (bitmask). We only parse strings.
    """
    try:
        result = subprocess.run(
            ["smartctl", "-n", "standby", "-i", dev],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"ERR [{dev}] smartctl timeout")
        return "unknown"
    except Exception as e:
        logger.error(f"ERR [{dev}] smartctl error: {e}")
        return "error"

    out = result.stdout or ""
    uout = out.upper()

    # Skip devices that clearly don't support SMART (e.g., virtual devices)
    for line in out.splitlines():
        if re.search(r"SMART support is:\s+Unavailable", line, flags=re.IGNORECASE):
            logger.info("INFO [%s] SMART unsupported; skipping", dev)
            return "unknown"

    if "STANDBY" in uout:
        return "standby"
    if "SLEEP" in uout:
        return "sleep"
    if "IDLE_A" in uout:
        return "idle_a"
    if "IDLE_B" in uout:
        return "idle_b"
    if "IDLE_C" in uout:
        return "idle_c"
    if "ACTIVE OR IDLE" in uout:
        return "active_or_idle"
    if "ACTIVE" in uout:
        return "active"
    if "IDLE" in uout:
        return "idle"

    return "unknown"


async def async_highest_power_state(  # NEW
    dev: str, attempts: int = PROBE_ATTEMPTS, interval_ms: int = PROBE_INTERVAL_MS
) -> str:
    """
    Probe smartctl multiple times (without waking the drive) and return the
    highest-activity state observed. Runs the sync smartctl parser in a thread.
    """
    highest = "unknown"
    for i in range(attempts):
        s = await asyncio.to_thread(smartctl_power_state, dev)
        highest = highest_activity_state(highest, s)
        if i + 1 < attempts and interval_ms > 0:
            await asyncio.sleep(interval_ms / 1000.0)
    return highest


async def gather_device_metrics(
    dev: str, pool_map: Dict[str, str]
) -> Optional[Dict[str, object]]:  # NEW
    """
    Process a single device and return a dict with metric lines and counters.
    Returns:
      {"lines": [...], "scanned_hdds": 1} OR {"skipped_non_rotational": 1} / {"skipped_virtual": 1}
    """
    # Only HDDs are monitored
    if not is_rotational(dev):
        return {"skipped_non_rotational": 1}

    # Skip QEMU/virtual devices explicitly
    if is_virtual_device(dev):
        return {"skipped_virtual": 1}

    dtype = get_rotational_type(dev)  # should be 'hdd' here, but keep label explicit
    # Map to pool by base device name (e.g., /dev/sdd from /dev/sdd1)
    base = re.sub(r"p?\d+$", "", dev)
    pool = pool_map.get(base, "none")

    device_id = get_persistent_id(dev)

    state = await async_highest_power_state(dev)
    value = STATE_MAP.get(state, STATE_MAP["unknown"])

    # Build metric lines for this device
    lines = [
        f'disk_info{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}"}} 1',
        f'disk_power_state{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}"}} {value}',
        f'disk_power_state_string{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}",state="{state}"}} 1',
    ]
    return {"lines": lines, "scanned_hdds": 1}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():  # CHANGED: async
    t0 = time.perf_counter()
    enumerated = 0
    skipped_non_rotational = 0
    skipped_virtual = 0
    scanned_hdds = 0

    lines = []
    # Headers first
    lines.append(
        "# HELP disk_power_state Current disk power state as a numeric code "
        "(0=standby, 7=sleep, 1=idle, 2=active_or_idle, -1=unknown, -2=error, "
        "3=idle_a, 4=idle_b, 5=idle_c, 6=active)."
    )
    lines.append("# TYPE disk_power_state gauge")
    lines.append(
        "# HELP disk_info Static labels describing the disk (type/pool). Always 1."
    )
    lines.append("# TYPE disk_info gauge")
    lines.append(
        "# HELP disk_power_state_string Always 1; carries the current power state as the 'state' label for display."
    )
    lines.append("# TYPE disk_power_state_string gauge")
    lines.append(
        "# HELP disk_exporter_scan_seconds Duration of the last scan in seconds."
    )
    lines.append("# TYPE disk_exporter_scan_seconds gauge")
    lines.append("# HELP disk_exporter_devices_total Devices seen / scanned / skipped.")
    lines.append("# TYPE disk_exporter_devices_total gauge")

    pool_map = get_zpool_device_map()

    devices = sorted(list_block_devices())
    enumerated = len(devices)

    # Run per-device probes concurrently, bounded by a semaphore
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run_with_sem(dev):
        async with sem:
            return await gather_device_metrics(dev, pool_map)

    tasks = [asyncio.create_task(run_with_sem(dev)) for dev in devices]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Collect results
    for r in results:
        if not r:
            continue
        skipped_non_rotational += r.get("skipped_non_rotational", 0)
        skipped_virtual += r.get("skipped_virtual", 0)
        scanned_hdds += r.get("scanned_hdds", 0)
        for line in r.get("lines", []):
            lines.append(line)

    duration = time.perf_counter() - t0

    lines.append(f"disk_exporter_scan_seconds {duration:.6f}")
    lines.append(f'disk_exporter_devices_total{{kind="enumerated"}} {enumerated}')
    lines.append(f'disk_exporter_devices_total{{kind="scanned_hdds"}} {scanned_hdds}')
    lines.append(
        f'disk_exporter_devices_total{{kind="skipped_non_rotational"}} {skipped_non_rotational}'
    )
    lines.append(
        f'disk_exporter_devices_total{{kind="skipped_virtual"}} {skipped_virtual}'
    )

    logger.info(
        "scan complete: enumerated=%d scanned_hdds=%d skipped_non_rotational=%d skipped_virtual=%d duration=%.3fs",
        enumerated,
        scanned_hdds,
        skipped_non_rotational,
        skipped_virtual,
        duration,
    )

    body = "\n".join(lines) + "\n"
    return Response(body, media_type="text/plain")
