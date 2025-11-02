# tests/test_zpool_map.py
import shutil
import subprocess

import main


def test_get_zpool_device_map_no_zpool(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda x: None)
    assert main.get_zpool_device_map() == {}


def test_get_zpool_device_map_parsing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda x: "/sbin/zpool")

    sample = """
  pool: tank
 state: ONLINE
config:

        NAME                                            STATE     READ WRITE CKSUM
        tank                                            ONLINE       0     0     0
          mirror-0                                      ONLINE       0     0     0
            /dev/disk/by-id/ata-DISK123-part1           ONLINE       0     0     0
            /dev/sdd1                                   ONLINE       0     0     0

errors: No known data errors
"""

    class FakeRun:
        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeRun(sample))

    # Also ensure realpath resolves the by-id to a real device
    monkeypatch.setattr(
        main.os.path,
        "realpath",
        lambda p: "/dev/sdc1" if "ata-DISK123-part1" in p else p,
    )

    m = main.get_zpool_device_map()
    # Both should be mapped to base device (partition stripped)
    assert m["/dev/sdc"] == "tank"
    assert m["/dev/sdd"] == "tank"
