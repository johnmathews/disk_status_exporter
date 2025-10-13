from fastapi import FastAPI, Response
import glob
import os
import re
import shutil
import subprocess
from typing import Dict

app = FastAPI()

STATE_MAP = {
    "standby": 0,
    "idle": 1,            # IDLE_B specifically
    "active_or_idle": 2,  # ACTIVE or IDLE (smartctl wording)
    "unknown": -1,
    "error": -2,
}

def device_type(dev: str) -> str:
    """
    Determine 'hdd' vs 'ssd' using smartctl -i first (does not spin up with -n standby),
    then /sys/block/*/queue/rotational. Be conservative; default to 'ssd' for NVMe.
    """
    # NVMe are non-rotational; short-circuit to SSD
    if dev.startswith("/dev/nvme"):
        return "ssd"

    try:
        proc = subprocess.run(
            ["smartctl", "-n", "standby", "-i", dev],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
        )
        out = proc.stdout or ""

        # Prefer explicit SSD signals
        if re.search(r"Rotation\s+Rate:\s+Solid\s+State\s+Device", out, re.IGNORECASE):
            return "ssd"
        if re.search(r"(Non[- ]rotating|SSD)", out, re.IGNORECASE):
            return "ssd"

        # Some adapters report "Rotation Rate: 0 rpm" for SSDs — treat as SSD
        m = re.search(r"Rotation\s+Rate:\s*(\d+)\s*rpm", out, re.IGNORECASE)
        if m:
            try:
                rpm = int(m.group(1))
                if rpm > 0:
                    return "hdd"
                else:
                    return "ssd"
            except ValueError:
                pass  # fall through
    except Exception:
        pass  # fall back to sysfs

    # Fallback: sysfs rotational flag
    basename = os.path.basename(dev)  # e.g. sda, sdb, nvme0n1
    path = f"/sys/block/{basename}/queue/rotational"
    try:
        with open(path) as f:
            return "hdd" if f.read().strip() == "1" else "ssd"
    except Exception:
        # Be conservative: unknown rather than claiming HDD
        return "unknown"

def have_zpool() -> bool:
    """We can try zpool only if binary exists and /dev/zfs is present."""
    return shutil.which("zpool") is not None and os.path.exists("/dev/zfs")

def get_zpool_device_map() -> Dict[str, str]:
    """
    Returns a best-effort map of /dev/sdX -> pool name using `zpool status -L`.
    Skips gracefully if zpool isn't available or /dev/zfs is missing.
    """
    pool_map: Dict[str, str] = {}
    if not have_zpool():
        return pool_map

    try:
        # -L to translate vdev paths (labels) if possible
        result = subprocess.run(
            ["zpool", "status", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result.check_returncode()

        current_pool = None
        in_config = False

        for raw in result.stdout.splitlines():
            line = raw.rstrip("\n")
            stripped = line.strip()

            if stripped.startswith("pool:"):
                current_pool = stripped.split(":", 1)[1].strip()
                in_config = False
                continue

            if stripped.startswith("config:"):
                in_config = True
                continue

            if not in_config or not current_pool:
                continue

            # Skip headings and virtual vdev labels
            if re.match(r"^(NAME|mirror-|special|logs|spare|cache|raidz|stripe)", stripped):
                continue

            # Capture devices like sda, sdd1, nvme0n1p1, etc.
            # We'll reduce partitions to their base disk (/dev/sda from /dev/sda1)
            m = re.match(r"^\s*([A-Za-z0-9]+)\S*\s+(ONLINE|DEGRADED|OFFLINE|UNAVAIL|REMOVED|FAULTED)", stripped)
            if not m:
                continue

            token = m.group(1)
            # reduce partitions: sda1 -> sda, nvme0n1p1 -> nvme0n1
            disk = re.sub(r"p?\d+$", "", token)
            pool_map[f"/dev/{disk}"] = current_pool

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
            timeout=10,
        )
        if proc.returncode != 0 and "Device READ LOG" not in proc.stderr:
            # Many controllers return nonzero for standby checks — don't be too strict.
            print(f"[{dev}] smartctl rc={proc.returncode} stderr={proc.stderr.strip()}")

        out = proc.stdout
        if "STANDBY" in out:
            return "standby"
        if "IDLE_B" in out:
            return "idle"
        if "ACTIVE or IDLE" in out:
            return "active_or_idle"

        # Skip non-physical/virtual disks with no SMART
        for line in out.splitlines():
            if re.search(r"SMART support is:\s+Unavailable", line):
                print(f"[{dev}] Skipped: SMART unsupported.")
                return "error"

        return "unknown"

    except subprocess.TimeoutExpired:
        print(f"[{dev}] smartctl timeout.")
        return "error"
    except Exception as e:
        print(f"[{dev}] smartctl error: {e}")
        return "error"

def list_block_devs() -> list[str]:
    devs = set()
    # Only classic SATA/SAS disks
    for p in glob.glob("/dev/sd?"):
        devs.add(p)
    for p in glob.glob("/dev/hd?"):
        devs.add(p)
    # If you *really* want to consider NVMe later, add them back – but we skip here.
    return sorted(devs)

@app.get("/metrics")
def metrics():
    lines = []
    pool_map = get_zpool_device_map()

    # Optional exposition metadata (safe to include, Prometheus ignores duplicates)
    lines.append('# HELP disk_power_state Drive power state as a one-hot label.')
    lines.append('# TYPE disk_power_state gauge')
    lines.append('# HELP disk_power_state_value Drive power state mapped to a number.')
    lines.append('# TYPE disk_power_state_value gauge')

    for dev in list_block_devs():
        dtype = device_type(dev)
        if dtype != "hdd":
            continue  # Only HDDs we can reasonably confirm

        state = smart_state_for(dev)
        if state == "error":
            # Only produce the numeric metric to make failures visible, skip the one-hot
            type_label = dtype
            pool_label = pool_map.get(dev, "none")
            lines.append(
                f'disk_power_state_value{{device="{dev}",type="{type_label}",pool="{pool_label}"}} {STATE_MAP["error"]}'
            )
            continue

        type_label = dtype
        pool_label = pool_map.get(dev, "none")

        # one-hot
        lines.append(
            f'disk_power_state{{device="{dev}",state="{state}",type="{type_label}",pool="{pool_label}"}} 1'
        )
        # numeric
        lines.append(
            f'disk_power_state_value{{device="{dev}",type="{type_label}",pool="{pool_label}"}} {STATE_MAP[state]}'
        )

    return Response("\n".join(lines) + "\n", media_type="text/plain")
