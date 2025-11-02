# tests/test_exporter_api.py
from fastapi.testclient import TestClient
import types

import main  # your exporter module


def test_healthz():
    client = TestClient(main.app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_happy_path(monkeypatch):
    """
    - 2 devices
    - both rotational, not virtual
    - distinct states returned
    - zpool mapping present
    """
    client = TestClient(main.app)

    monkeypatch.setattr(main, "list_block_devices", lambda: ["/dev/sda", "/dev/sdb"])
    monkeypatch.setattr(main, "is_rotational", lambda dev: True)
    monkeypatch.setattr(main, "is_virtual_device", lambda dev: False)
    monkeypatch.setattr(main, "get_rotational_type", lambda dev: "hdd")
    monkeypatch.setattr(
        main,
        "get_persistent_id",
        lambda dev: f"/dev/disk/by-id/FAKE-{dev.split('/')[-1]}",
    )
    monkeypatch.setattr(
        main, "get_zpool_device_map", lambda: {"/dev/sda": "tank", "/dev/sdb": "backup"}
    )

    async def fake_highest(dev, *_, **__):
        return {"sda": "standby", "sdb": "active"}[dev.split("/")[-1]]

    monkeypatch.setattr(main, "async_highest_power_state", fake_highest)

    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text

    # headers present
    assert "# TYPE disk_power_state gauge" in body
    assert "# TYPE disk_info gauge" in body
    assert "# TYPE disk_power_state_string gauge" in body

    # sda metrics
    assert (
        'disk_info{device_id="/dev/disk/by-id/FAKE-sda",device="/dev/sda",type="hdd",pool="tank"} 1'
        in body
    )
    assert (
        'disk_power_state_string{device_id="/dev/disk/by-id/FAKE-sda",device="/dev/sda",type="hdd",pool="tank",state="standby"} 1'
        in body
    )

    # sdb metrics
    assert (
        'disk_info{device_id="/dev/disk/by-id/FAKE-sdb",device="/dev/sdb",type="hdd",pool="backup"} 1'
        in body
    )
    assert (
        'disk_power_state_string{device_id="/dev/disk/by-id/FAKE-sdb",device="/dev/sdb",type="hdd",pool="backup",state="active"} 1'
        in body
    )

    # device counters
    assert 'disk_exporter_devices_total{kind="enumerated"} 2' in body
    assert 'disk_exporter_devices_total{kind="scanned_hdds"} 2' in body
    assert 'disk_exporter_devices_total{kind="skipped_non_rotational"} 0' in body
    assert 'disk_exporter_devices_total{kind="skipped_virtual"} 0' in body


def test_metrics_skip_non_rotational_and_virtual(monkeypatch):
    client = TestClient(main.app)

    # 3 devices: one SSD, one virtual, one real HDD
    monkeypatch.setattr(
        main, "list_block_devices", lambda: ["/dev/sda", "/dev/sdb", "/dev/sdc"]
    )

    def fake_is_rotational(dev):
        return dev != "/dev/sda"  # sda -> SSD (non-rotational)

    monkeypatch.setattr(main, "is_rotational", fake_is_rotational)
    monkeypatch.setattr(main, "is_virtual_device", lambda dev: dev == "/dev/sdb")
    monkeypatch.setattr(
        main, "get_rotational_type", lambda dev: "hdd" if dev != "/dev/sda" else "ssd"
    )
    monkeypatch.setattr(
        main,
        "get_persistent_id",
        lambda dev: f"/dev/disk/by-id/FAKE-{dev.split('/')[-1]}",
    )
    monkeypatch.setattr(main, "get_zpool_device_map", lambda: {})

    async def fake_highest(dev, *_, **__):
        return "idle"

    monkeypatch.setattr(main, "async_highest_power_state", fake_highest)

    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text

    # enumerated 3; scanned only /dev/sdc
    assert 'disk_exporter_devices_total{kind="enumerated"} 3' in body
    assert 'disk_exporter_devices_total{kind="skipped_non_rotational"} 1' in body  # sda
    assert 'disk_exporter_devices_total{kind="skipped_virtual"} 1' in body  # sdb
    assert 'disk_exporter_devices_total{kind="scanned_hdds"} 1' in body  # sdc

    # ensure only sdc appears in metrics
    assert 'device="/dev/sdc"' in body
    assert 'device="/dev/sda"' not in body
    assert 'device="/dev/sdb"' not in body
