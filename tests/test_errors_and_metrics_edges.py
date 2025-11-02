# tests/test_errors_and_metrics_edges.py
from fastapi.testclient import TestClient
import subprocess

import main


def test_smartctl_timeout(monkeypatch, caplog):
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="smartctl", timeout=10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert main.smartctl_power_state("/dev/sdz") == "unknown"
    # Optional: assert a timeout log was written
    assert any("smartctl timeout" in rec.getMessage() for rec in caplog.records)


def test_smartctl_smart_unsupported(monkeypatch):
    class FakeRun:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0
            self.stderr = ""

    out = "SMART support is: Unavailable\nsome other lines"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeRun(out))
    assert main.smartctl_power_state("/dev/sdz") == "unknown"


def test_metrics_no_devices(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "list_block_devices", lambda: [])
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert 'disk_exporter_devices_total{kind="enumerated"} 0' in body
    assert 'disk_exporter_devices_total{kind="scanned_hdds"} 0' in body


def test_metrics_unknown_state_fallback(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "list_block_devices", lambda: ["/dev/sdy"])
    monkeypatch.setattr(main, "is_rotational", lambda d: True)
    monkeypatch.setattr(main, "is_virtual_device", lambda d: False)
    monkeypatch.setattr(main, "get_rotational_type", lambda d: "hdd")
    monkeypatch.setattr(main, "get_persistent_id", lambda d: "/dev/disk/by-id/FAKE-sdy")
    monkeypatch.setattr(main, "get_zpool_device_map", lambda: {})

    async def fake_highest(*a, **kw):
        return "totally_new_state"

    monkeypatch.setattr(main, "async_highest_power_state", fake_highest)

    r = client.get("/metrics")
    assert r.status_code == 200

    body = r.text
    # unknown -> STATE_MAP fallback to -1; label order includes device_id before device
    lines = body.splitlines()
    ps_line = next(
        (
            ln
            for ln in lines
            if ln.startswith("disk_power_state{") and 'device="/dev/sdy"' in ln
        ),
        None,
    )
    assert (
        ps_line is not None
    ), f"disk_power_state line for /dev/sdy not found.\nBody:\n{body}"
    assert ps_line.strip().endswith(
        " -1"
    ), f"Expected value -1 for unknown state, got: {ps_line}"

    # And the string metric should carry the original unknown state label
    assert "disk_power_state_string" in body
    assert 'state="totally_new_state"' in body
