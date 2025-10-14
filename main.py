# disk-status-exporter/main.py

from fastapi import FastAPI, Response
import os
import glob
import re
import shutil
import subprocess
from typing import Dict, Iterable, Optional

app = FastAPI()

# Single numeric gauge keeps things simple. Use disk_info{} for metadata joins.
STATE_MAP: Dict[str, int] = {
    "standby": 0,
    "idle": 1,              # IDLE_B maps to idle
    "active_or_idle": 2,    # ACTIVE or IDLE (can't distinguish further)
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
        # Skip virtual and mapper/loop devices
        if kname.startswith(("loop", "ram", "fd")) or kname.startswith(("dm-",)):
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
    candidates.sort(key=lambda n: (
        0 if n.startswith(PREFERRED_ID_PREFIX) else 1,
        len(n),
        n
    ))
    return f"/dev/disk/by-id/{candidates[0]}"


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
                base = re.sub(r"[0-9]+$", "", real)
                pool_map[base] = current_pool
            else:
                base = re.sub(r"[0-9]+$", "", devpath)
                pool_map[base] = current_pool

    except Exception as e:
        print(f"[zpool] skipped (error: {e})")
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
        print(f"[{dev}] smartctl timeout")
        return "unknown"
    except Exception as e:
        print(f"[{dev}] smartctl error: {e}")
        return "error"

    out = result.stdout or ""

    # Skip devices that clearly don't support SMART (e.g., virtual devices)
    for line in out.splitlines():
        if re.search(r"SMART support is:\s+Unavailable", line):
            print(f"[{dev}] SMART unsupported; skipping")
            return "unknown"

    if "STANDBY" in out:
        return "standby"
    if "IDLE_B" in out:
        return "idle"
    if "ACTIVE or IDLE" in out:
        return "active_or_idle"

    return "unknown"


@app.get("/metrics")
def metrics():
    lines = []
    # Headers first
    lines.append("# HELP disk_power_state Current disk power state as a numeric code (0=standby, 1=idle, 2=active_or_idle, -1=unknown, -2=error).")
    lines.append("# TYPE disk_power_state gauge")
    lines.append("# HELP disk_info Static labels describing the disk (type/pool). Always 1.")
    lines.append("# TYPE disk_info gauge")

    pool_map = get_zpool_device_map()

    for dev in sorted(list_block_devices()):
        # Only HDDs are monitored
        if not is_rotational(dev):
            continue

        dtype = get_rotational_type(dev)  # should be 'hdd' here, but keep label explicit
        # Map to pool by base device name (e.g., /dev/sdd from /dev/sdd1)
        base = re.sub(r"[0-9]+$", "", dev)
        pool = pool_map.get(base, "none")

        device_id = get_persistent_id(dev)

        state = smartctl_power_state(dev)
        value = STATE_MAP.get(state, STATE_MAP["unknown"])

        # info metric (1) to carry metadata labels for joins
        lines.append(
            f'disk_info{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}"}} 1'
        )

        # primary numeric state metric
        lines.append(
            f'disk_power_state{{device_id="{device_id}",device="{dev}",type="{dtype}",pool="{pool}"}} {value}'
        )

    body = "\n".join(lines) + "\n"
    return Response(body, media_type="text/plain")
