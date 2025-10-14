# disk-status-exporter/main.py

from fastapi import FastAPI, Response
import glob
import os
import re
import shutil
import subprocess
import time
from typing import Dict, Optional

app = FastAPI()

# ---- Config via env ----
SMART_TIMEOUT = float(os.getenv("SMART_TIMEOUT", "10"))
ZPOOL_TIMEOUT = float(os.getenv("ZPOOL_TIMEOUT", "5"))
CACHE_TTL = float(os.getenv("SMARTCACHE_TTL", "8"))

STATE_MAP = {
    "standby": 0,
    "idle": 1,            # IDLE_B specifically
    "active_or_idle": 2,  # ACTIVE or IDLE (smartctl wording)
    "unknown": -1,
    "error": -2,
}

# tiny in-process cache: dev -> (timestamp, state)
_cache: dict[str, tuple[float, str]] = {}


def have_zpool() -> bool:
    """We can try zpool only if binary exists and /dev/zfs is present."""
    return shutil.which("zpool") is not None and os.path.exists("/dev/zfs")


def _base_disk_from_path(p: str) -> Optional[str]:
    """
    Normalize a device-ish path to its base disk:
      /dev/disk/by-id/...  -> /dev/sdX or /dev/nvme0n1p1 -> /dev/nvme0n1
      bare names (e.g. 'sda1') will be interpreted under /dev/.
    Returns /dev/<disk> or None if unrecognized.
    """
    path = p
    if not path.startswith("/dev/"):
        path = f"/dev/{path}"
    real = os.path.realpath(path)

    # match base device (strip partition suffixes)
    m = re.match(r"^/dev/(sd[a-z]+|hd[a-z]+|nvme\d+n\d+)", real)
    if m:
        return f"/dev/{m.group(1)}"
    return None


def get_zpool_device_map() -> Dict[str, str]:
    """
    Returns a best-effort map of /dev/<base-disk> -> pool name using `zpool status -LP`.
    Skips gracefully if zpool isn't available or /dev/zfs is missing.
    """
    pool_map: Dict[str, str] = {}
    if not have_zpool():
        return pool_map

    try:
        # -L translate labels; -P print full paths
        result = subprocess.run(
            ["zpool", "status", "-LP"],
            capture_output=True,
            text=True,
            timeout=ZPOOL_TIMEOUT,
        )
        result.check_returncode()

        current_pool = None
        in_config = False

        for raw in result.stdout.splitlines():
            s = raw.strip()

            if s.startswith("pool:"):
                current_pool = s.split(":", 1)[1].strip()
                in_config = False
                continue

            if s.startswith("config:"):
                in_config = True
                continue

            if not in_config or not current_pool:
                continue

            # Skip headings and virtual vdevs
            if re.match(r"^(NAME|mirror-|special|logs|spare|cache|raidz|stripe)", s):
                continue

            # First column is device-ish token
            tok = s.split()[0] if s else ""
            if not tok:
                continue

            base = _base_disk_from_path(tok)
            if base:
                pool_map[base] = current_pool

    except subprocess.TimeoutExpired:
        print("zpool status timed out")
    except subprocess.CalledProcessError as e:
        print(f"zpool status failed: rc={e.returncode} {e.stderr}")
    except Exception as e:
        print(f"Error parsing zpool status: {e}")

    return pool_map


def smart_state_for(dev: str) -> str:
    """
    Use smartctl to detect power state without spinning up the drive.
    Returns one of STATE_MAP keys.
    """
    try:
        # -n standby avoids spinning up if in standby; -i prints basic info
        proc = subprocess.run(
            ["smartctl", "-n", "standby", "-i", dev],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=SMART_TIMEOUT,
        )

        # Some controllers return non-zero for standby checks—don't be strict
        if proc.returncode != 0 and "Device READ LOG" not in (proc.stderr or ""):
            print(f"[{dev}] smartctl rc={proc.returncode} stderr={proc.stderr.strip()}")

        out = proc.stdout or ""

        if "STANDBY" in out:
            return "standby"
        if "IDLE_B" in out:
            return "idle"
        if "ACTIVE or IDLE" in out:
            return "active_or_idle"

        # If SMART is unavailable (USB bridge/virtual), treat as unknown (not error)
        for line in out.splitlines():
            if re.search(r"SMART support is:\s+Unavailable", line):
                return "unknown"

        # Could not confidently classify
        return "unknown"

    except subprocess.TimeoutExpired:
        print(f"[{dev}] smartctl timeout.")
        return "error"
    except Exception as e:
        print(f"[{dev}] smartctl error: {e}")
        return "error"


def smart_state_cached(dev: str) -> str:
    now = time.time()
    ts_val = _cache.get(dev)
    if ts_val and now - ts_val[0] < CACHE_TTL:
        return ts_val[1]
    s = smart_state_for(dev)
    _cache[dev] = (now, s)
    return s


def list_block_devs() -> list[str]:
    """
    Discover whole-disk block devices via /sys/block and return rotational disks only.
    Skips loop/ram/dm/md; returns /dev/<disk> without partitions.
    """
    devs: list[str] = []
    try:
        for name in os.listdir("/sys/block"):
            if name.startswith(("loop", "ram", "dm-", "md")):
                continue
            rot_path = f"/sys/block/{name}/queue/rotational"
            try:
                with open(rot_path) as f:
                    # only rotational (HDDs)
                    if f.read().strip() != "1":
                        continue
            except FileNotFoundError:
                continue

            dev_path = f"/dev/{name}"
            if os.path.exists(dev_path):
                devs.append(dev_path)
    except Exception as e:
        # Fallback to classic globs (best-effort) if sysfs reading fails
        print(f"/sys/block scan failed: {e}; falling back to /dev globs")
        for p in glob.glob("/dev/sd?"):
            devs.append(p)
        for p in glob.glob("/dev/hd?"):
            devs.append(p)

    return sorted(set(devs))


@app.get("/metrics")
def metrics():
    lines = []
    pool_map = get_zpool_device_map()

    # Exposition metadata (Prometheus ignores duplicates)
    lines.append('# HELP disk_power_state Drive power state as a one-hot label.')
    lines.append('# TYPE disk_power_state gauge')
    lines.append('# HELP disk_power_state_value Drive power state mapped to a number.')
    lines.append('# TYPE disk_power_state_value gauge')

    for dev in list_block_devs():
        # type label is always "hdd" here (we filtered rotational=1)
        type_label = "hdd"
        pool_label = pool_map.get(dev, "none")

        state = smart_state_cached(dev)

        # Always emit one-hot row for the observed state
        lines.append(
            f'disk_power_state{{device="{dev}",state="{state}",type="{type_label}",pool="{pool_label}"}} 1'
        )
        # Always emit numeric value
        state_value = STATE_MAP.get(state, STATE_MAP["unknown"])
        lines.append(
            f'disk_power_state_value{{device="{dev}",type="{type_label}",pool="{pool_label}"}} {state_value}'
        )

    return Response("\n".join(lines) + "\n", media_type="text/plain")
