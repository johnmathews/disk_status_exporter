# tests/test_smartctl_and_state.py
import asyncio
import types
from unittest.mock import patch

import main


class FakeRun:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_smartctl_parsing_standby():
    with patch("subprocess.run", return_value=FakeRun("Device is in STANDBY mode")):
        assert main.smartctl_power_state("/dev/sdx") == "standby"


def test_smartctl_parsing_sleep():
    with patch("subprocess.run", return_value=FakeRun("Device is in SLEEP mode")):
        assert main.smartctl_power_state("/dev/sdx") == "sleep"


def test_smartctl_parsing_active_or_idle_variants():
    with patch(
        "subprocess.run", return_value=FakeRun("drive state is: ACTIVE or IDLE")
    ):
        assert main.smartctl_power_state("/dev/sdx") == "active_or_idle"
    with patch("subprocess.run", return_value=FakeRun("state: ACTIVE/IDLE")):
        assert main.smartctl_power_state("/dev/sdx") == "active_or_idle"


def test_smartctl_parsing_idle_and_active():
    with patch("subprocess.run", return_value=FakeRun("current mode: IDLE")):
        assert main.smartctl_power_state("/dev/sdx") == "idle"
    with patch("subprocess.run", return_value=FakeRun("current mode: ACTIVE")):
        assert main.smartctl_power_state("/dev/sdx") == "active"


def test_smartctl_parsing_unknown_on_error_rc():
    # unknown when no tokens AND nonzero RC
    with patch("subprocess.run", return_value=FakeRun("some text", returncode=2)):
        assert main.smartctl_power_state("/dev/sdx") == "unknown"


def test_smartctl_parsing_default_to_active_or_idle_on_success_rc():
    # success RC but no explicit tokens => active_or_idle
    with patch(
        "subprocess.run", return_value=FakeRun("model: FooBar 123", returncode=0)
    ):
        assert main.smartctl_power_state("/dev/sdx") == "active_or_idle"


def test_async_highest_power_state(monkeypatch):
    seq = iter(
        ["standby", "idle_b", "active_or_idle"]
    )  # highest should be active_or_idle

    def fake_sync(dev):
        return next(seq)

    monkeypatch.setattr(main, "smartctl_power_state", fake_sync)
    res = asyncio.run(
        main.async_highest_power_state("/dev/sdx", attempts=3, interval_ms=0)
    )

    assert res == "active_or_idle"
