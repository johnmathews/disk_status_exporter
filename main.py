# disk-status-exporter/main.py

# monitor the status of various HDDs and create prometheus metrics showing if they are in standby, idle, etc.

from fastapi import FastAPI, Response
import os
import glob
import re
import shutil
import subprocess
from typing import Dict, Iterable, Optional
import time
import logging

app = FastAPI()

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


# Numeric mapping kept for backward compatibility with existing Prometheus rules.
STATE_MAP: Dict[str, int] = {
    "standby": 0,
    "idle": 1,  # IDLE / IDLE_A / IDLE_B / IDLE_C map to idle
    "active_or_idle": 2,  # ACTIVE or IDLE (cannot distinguish further)
    "unknown": -1,
    "error": -2,
}

PREFERRED_ID_PREFIX = ("ata-", "scsi-", "wwn-", "nvme-", "usb-", "virtio-")


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

    candidates.sort(
        key=lambda n: (0 if n.startswith(PREFERRED_ID_PREFIX) else 1, len(n), n)
    )
    return f"/dev/disk/by-id/{candidates[0]}"


def is_virtual_device(dev: str) -> bool:
    """
    Heuristics to filter out QEMU/virtual devices.
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

            m = re.match(r"^(/dev/\S+)", s)
            if not m:
                continue

            devpath = m.group(1)
            # Strip partition suffix for base disk, but keep /dev/disk/by-id paths
            if devpath.startswith("/dev/disk/by-id/"):
                real = os.path.realpath(devpath)
                base = re.sub(r"[0-9]+$", "", real)
                pool_map[base] = current_pool
            else:
                base = re.sub(r"[0-9]+$", "", devpath)
                pool_map[base] = current_pool

    except Exception as e:
        print(f"ERR [zpool] skipped (error: {e})")
        return {}

    return pool_map


def smartctl_power_mode_raw(dev: str) -> str:
    """
    Return the raw power mode string from smartctl stdout without waking the disk.
    Example values: STANDBY, IDLE_A, IDLE_B, IDLE_C, IDLE, ACTIVE or IDLE, SLEEP.
    Returns 'UNKNOWN' or 'ERROR' when appropriate.
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
        print(f"ERR [{dev}] smartctl timeout")
        return "UNKNOWN"
    except Exception as e:
        print(f"ERR [{dev}] smartctl error: {e}")
        return "ERROR"

    out = result.stdout or ""
    m = re.search(r"Power mode (?:is|was):\s*(.+)", out)
    if m:
        return m.group(1).strip()
    return "UNKNOWN"


def normalize_mode_for_numeric(raw: str) -> str:
    """
    Map raw smartctl mode back to the existing numeric categories.
    SLEEP is treated like STANDBY (spun down).
    """
    u = (raw or "").upper()
    if u in ("STANDBY", "SLEEP"):
        return "standby"
    if u in ("IDLE", "IDLE_A", "IDLE_B", "IDLE_C"):
        return "idle"
    if u == "ACTIVE OR IDLE":
        return "active_or_idle"
    if u == "ERROR":
        return "error"
    return "unknown"


def prom_escape_label_value(s: str) -> str:
    """
    Escape a string for Prometheus label value context:
    backslash, double-quote, and newline.
    """
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    t0 = time.perf_counter()
    enumerated = 0
    skipped_non_rotational = 0
    skipped_virtual = 0
    scanned_hdds = 0

    lines = []
    # Headers first
    lines.append(
        "# HELP disk_power_state Current disk power state as a numeric code (0=standby, 1=idle, 2=active_or_idle, -1=unknown, -2=error)."
    )
    lines.append("# TYPE disk_power_state gauge")
    lines.append(
        "# HELP disk_power_mode_info Disk power mode as reported by smartctl (label state=...). Always 1."
    )
    lines.append("# TYPE disk_power_mode_info gauge")
    lines.append(
        "# HELP disk_info Static labels describing the disk (type/pool). Always 1."
    )
    lines.append("# TYPE disk_info gauge")

    pool_map = get_zpool_device_map()

    devices = sorted(list_block_devices())
    enumerated = len(devices)
    for dev in devices:
        # Only HDDs are monitored
        if not is_rotational(dev):
            skipped_non_rotational += 1
            continue

        # Skip QEMU/virtual devices explicitly
        if is_virtual_device(dev):
            skipped_virtual += 1
            continue

        scanned_hdds += 1

        dtype = get_rotational_type(dev)  # should be 'hdd' here
        base = re.sub(r"[0-9]+$", "", dev)
        pool = pool_map.get(base, "none")
        device_id = get_persistent_id(dev)

        raw_mode = smartctl_power_mode_raw(dev)
        norm_key = normalize_mode_for_numeric(raw_mode)
        value = STATE_MAP.get(norm_key, STATE_MAP["unknown"])

        # info metric (1) to carry metadata labels for joins
        lines.append(
            f'disk_info{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}"}} 1'
        )

        # new textual mode metric (always 1), with state label (escaped)
        lines.append(
            f'disk_power_mode_info{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}",state="{prom_escape_label_value(raw_mode)}"}} 1'
        )

        # primary numeric state metric (unchanged for compatibility)
        lines.append(
            f'disk_power_state{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}"}} {value}'
        )

    duration = time.perf_counter() - t0
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
