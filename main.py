from fastapi import FastAPI, Response
import subprocess
import glob

app = FastAPI()



@app.get("/metrics")
def get_metrics():
    metrics = []

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

        metrics.append(f'disk_power_state{{device="{dev}",state="{state}"}} 1')

        state_map = {
            "standby": 0,
            "active_or_idle": 1,
            "unknown": -1,
            "error": -2,
        }
        metrics.append(f'disk_power_state_value{{device="{dev}"}} {state_map[state]}')

    return Response("\n".join(metrics) + "\n", media_type="text/plain")
