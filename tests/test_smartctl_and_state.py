# tests/test_smartctl_and_state.py
import asyncio
import types
from unittest.mock import patch
import pytest

import main


@pytest.fixture(autouse=True)
def clear_cooldowns():
    """Clear device cooldowns before and after each test."""
    main._device_cooldowns.clear()
    yield
    main._device_cooldowns.clear()


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


def test_smartctl_command_includes_sat_flag():
    """Verify smartctl is called with -d sat,12 to skip autodetection."""
    with patch("subprocess.run", return_value=FakeRun("Device is in STANDBY mode")) as mock_run:
        main.smartctl_power_state("/dev/sdx")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-d" in cmd
        assert "sat,12" in cmd


def test_cooldown_not_in_cooldown_initially():
    """Device not in cooldown should return False."""
    assert main.is_device_in_cooldown("/dev/sdy") is False


def test_cooldown_set_and_check():
    """After setting cooldown, device should be in cooldown."""
    main.set_device_cooldown("/dev/sdz")
    assert main.is_device_in_cooldown("/dev/sdz") is True


def test_cooldown_expires(monkeypatch):
    """Cooldown should expire after COOLDOWN_SECONDS."""
    import time

    # Set cooldown at time 1000
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    main.set_device_cooldown("/dev/sda")

    # Still in cooldown at time 1000 + COOLDOWN_SECONDS - 1
    monkeypatch.setattr(time, "time", lambda: 1000.0 + main.COOLDOWN_SECONDS - 1)
    assert main.is_device_in_cooldown("/dev/sda") is True

    # Expired at time 1000 + COOLDOWN_SECONDS + 1
    monkeypatch.setattr(time, "time", lambda: 1000.0 + main.COOLDOWN_SECONDS + 1)
    assert main.is_device_in_cooldown("/dev/sda") is False
    # Should also be removed from dict
    assert "/dev/sda" not in main._device_cooldowns


def test_smartctl_skips_device_in_cooldown():
    """smartctl_power_state should return 'unknown' for devices in cooldown."""
    main.set_device_cooldown("/dev/sdb")

    with patch("subprocess.run") as mock_run:
        result = main.smartctl_power_state("/dev/sdb")
        assert result == "unknown"
        # subprocess.run should NOT have been called
        mock_run.assert_not_called()


def test_smartctl_timeout_triggers_cooldown():
    """Timeout should put device into cooldown."""
    import subprocess

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("smartctl", 10)):
        result = main.smartctl_power_state("/dev/sdc")
        assert result == "unknown"
        assert "/dev/sdc" in main._device_cooldowns
