# tests/test_list_ids_virtual.py
import os
import glob
import builtins
import types

import main


def test_list_block_devices_filters(monkeypatch):
    # Simulate /sys/block entries and existence of some /dev nodes
    monkeypatch.setattr(
        os.path, "isdir", lambda p: True if p == "/sys/block" else os.path.isdir(p)
    )
    sys_entries = [
        "loop0",
        "ram0",
        "fd0",
        "sr0",
        "md0",
        "zd16",
        "dm-0",
        "sda",
        "nvme0n1",
        "vda",
    ]
    monkeypatch.setattr(
        os, "listdir", lambda p: sys_entries if p == "/sys/block" else os.listdir(p)
    )

    def fake_exists(p):
        # only mark these as existing device nodes
        return p in {"/dev/sda", "/dev/nvme0n1", "/dev/vda"}

    monkeypatch.setattr(os.path, "exists", fake_exists)

    devs = list(main.list_block_devices())
    # Only physical-ish block devices should survive
    assert devs == ["/dev/nvme0n1", "/dev/sda", "/dev/vda"] or devs == [
        "/dev/sda",
        "/dev/nvme0n1",
        "/dev/vda",
    ]


def test_get_rotational_type_ok_and_error(monkeypatch):
    # happy path
    def fake_open_ok(path, mode="r", *a, **kw):
        class F:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return "1\n" if path.endswith("/rotational") else ""

        return F()

    monkeypatch.setattr(builtins, "open", fake_open_ok)
    assert main.get_rotational_type("/dev/sda") == "hdd"

    # error path -> unknown
    def fake_open_raises(*a, **kw):
        raise OSError("boom")

    monkeypatch.setattr(builtins, "open", fake_open_raises)
    assert main.get_rotational_type("/dev/sda") == "unknown"


def test_get_persistent_id_variants(monkeypatch, tmp_path):
    # Case 1: /dev/disk/by-id does not exist -> returns dev
    monkeypatch.setattr(
        os.path, "isdir", lambda p: False if p == "/dev/disk/by-id" else True
    )
    assert main.get_persistent_id("/dev/sdz") == "/dev/sdz"

    # Case 2: by-id exists but no candidates -> fallback to dev
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(glob, "glob", lambda p: [])
    assert main.get_persistent_id("/dev/sdz") == "/dev/sdz"

    # Case 3: multiple candidates, prefer prefixed & shorter
    # Simulate that both symlinks resolve to /dev/sdz
    def fake_realpath(p):
        return "/dev/sdz" if p.startswith("/dev/disk/by-id/") else p

    monkeypatch.setattr(os.path, "realpath", fake_realpath)
    monkeypatch.setattr(
        glob,
        "glob",
        lambda p: [
            "/dev/disk/by-id/xyz-long-generic-id-123",
            "/dev/disk/by-id/ata-NICE",
            "/dev/disk/by-id/wwn-FAIR",
        ],
    )
    chosen = main.get_persistent_id("/dev/sdz")
    # Should prefer ata-/wwn- prefix; with our sort logic ata- wins over wwn-
    assert chosen == "/dev/disk/by-id/ata-NICE"


def test_is_virtual_device_by_prefix(monkeypatch):
    # Skip file reads; base_id check alone should classify as virtual
    monkeypatch.setattr(
        main, "get_persistent_id", lambda dev: "/dev/disk/by-id/scsi-0QEMU_FAKE"
    )

    # Force vendor/model reads to return empty (so only base_id rule applies)
    def fake_open(*a, **kw):
        raise OSError("nope")

    monkeypatch.setattr(builtins, "open", fake_open)
    assert main.is_virtual_device("/dev/vda") is True

    # Non-virtual id
    monkeypatch.setattr(
        main, "get_persistent_id", lambda dev: "/dev/disk/by-id/ata-REAL"
    )
    assert main.is_virtual_device("/dev/sda") is False
