from fastapi import FastAPI, Response
import subprocess
import glob

import re
import subprocess

app = FastAPI()

def get_rotational_type(dev):
    dev_short = dev.replace("/dev/", "")
    try:
        with open(f"/sys/block/{dev_short}/queue/rotational") as f:
            return "hdd" if f.read().strip() == "1" else "ssd"
    except Exception:
        return "unknown"

def get_zpool_partition_map():
    """Returns a map of /dev/sdXn -> pool name"""
    partition_map = {}
    try:
        result = subprocess.run(["zpool", "status", "-L"], capture_output=True, text=True, timeout=5)
        current_pool = None
        in_config_section = False

        for line in result.stdout.splitlines():
            stripped = line.strip()

            if stripped.startswith("pool:"):
                current_pool = stripped.split(":", 1)[1].strip()
                in_config_section = False
                continue

            if stripped.startswith("config:"):
                in_config_section = True
                continue

            if in_config_section:
                # Skip headers and pool layout lines
                if re.match(r"^(NAME|mirror-|special|logs|cache|spare|raidz|stripe)", stripped):
                    continue

                # Match partition-like device names, e.g. sdc1, sdd2, sda3
                match = re.match(r"^\s*([a-zA-Z0-9]+[0-9]+)\s+ONLINE", line)
                if match and current_pool:
                    part = match.group(1)
                    partition_map[f"/dev/{part}"] = current_pool
    except Exception as e:
        print(f"Error parsing zpool status: {e}")
    return partition_map


def get_zpool_device_map():
    """Returns a map of /dev/sdX -> pool name based on zpool status"""
    pool_map = {}
    try:
        result = subprocess.run(["zpool", "status", "-L"], capture_output=True, text=True, timeout=5)
        current_pool = None
        in_config_section = False

        for line in result.stdout.splitlines():
            if line.strip().startswith("pool:"):
                current_pool = line.split(":", 1)[1].strip()
                in_config_section = False
                continue

            if line.strip().startswith("config:"):
                in_config_section = True
                continue

            if in_config_section:
                # Ignore section headers (like NAME, mirror-0, logs, etc.)
                if re.match(r"^\s*(NAME|mirror-|special|logs|spare|cache|raidz|stripe)", line.strip()):
                    continue

                # Match lines that contain a device like sdd1 or nvme0n1p1
                dev_match = re.match(r"^\s+([a-zA-Z0-9]+[0-9]+)\s", line)
                if dev_match and current_pool:
                    part = dev_match.group(1)
                    # Strip trailing digits to get disk base (e.g. sdd1 → sdd)
                    disk = re.sub(r"[0-9]+$", "", part)
                    pool_map[f"/dev/{disk}"] = current_pool
    except Exception as e:
        print(f"Error parsing zpool status: {e}")
    return pool_map


@app.get("/metrics")
def get_metrics():
    metrics = []
    partition_map = get_zpool_device_map()
    
    state_map = {
        "standby": 0,
        "active_or_idle": 1,
        "unknown": -1,
        "error": -2,
    }

    for dev in sorted(glob.glob("/dev/sd?")):
        try:
            result = subprocess.run(
                ["smartctl", "-n", "standby", "-i", dev],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            if "STANDBY" in result.stdout:
                state = "standby"
            elif "ACTIVE or IDLE" in result.stdout:
                state = "active_or_idle"
            else:
                state = "unknown"
        except Exception:
            state = "error"

        type_label = get_rotational_type(dev)
        pool_label = partition_map.get(dev, "none")

        metrics.append(
            f'disk_power_state{{device="{dev}",state="{state}",type="{type_label}",pool="{pool_label}"}} 1'
        )
        metrics.append(
            f'disk_power_state_value{{device="{dev}",type="{type_label}",pool="{pool_label}"}} {state_map[state]}'
        )

    return Response("\n".join(metrics) + "\n", media_type="text/plain")
