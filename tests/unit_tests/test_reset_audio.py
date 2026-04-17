from __future__ import annotations

from typing import Any

from reachy_mini.tools import reset_audio


class FakeReSpeaker:
    def __init__(self, version: list[int] | None = None):
        self.version = version or [0, 2, 1, 2]
        self.writes: list[tuple[str, list[int]]] = []
        self.closed = False

    def read(self, name: str) -> Any:
        assert name == "VERSION"
        return self.version

    def write(self, name: str, values: list[int]) -> None:
        self.writes.append((name, values))

    def close(self) -> None:
        self.closed = True


class ExecCalled(Exception):
    def __init__(self, file: str, argv: list[str]):
        super().__init__(file, *argv)
        self.file = file
        self.argv = argv



def test_strip_remainder_separator():
    assert reset_audio._strip_remainder_separator(["--", "--no-wake-up-on-start"]) == [
        "--no-wake-up-on-start"
    ]
    assert reset_audio._strip_remainder_separator(["--log-level", "DEBUG"]) == [
        "--log-level",
        "DEBUG",
    ]



def test_wait_for_respeaker_retries_until_found(monkeypatch):
    fake = FakeReSpeaker()
    results = iter([None, None, fake])
    monotonic_values = iter([0.0, 0.1, 0.2, 0.3])
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        "reachy_mini.tools.reset_audio.time.monotonic", lambda: next(monotonic_values)
    )

    found = reset_audio._wait_for_respeaker(
        lambda: next(results),
        timeout_seconds=2.0,
        sleep_fn=sleep_calls.append,
    )

    assert found is fake
    assert sleep_calls == [reset_audio.POLL_INTERVAL_SECONDS, reset_audio.POLL_INTERVAL_SECONDS]



def test_main_reset_only(monkeypatch):
    before = FakeReSpeaker([0, 2, 1, 2])
    after = FakeReSpeaker([0, 2, 1, 3])
    sleep_calls: list[float] = []

    monkeypatch.setattr("reachy_mini.tools.reset_audio.init_respeaker_usb", lambda: before)
    monkeypatch.setattr("reachy_mini.tools.reset_audio._wait_for_respeaker", lambda *args, **kwargs: after)
    monkeypatch.setattr("reachy_mini.tools.reset_audio.time.sleep", sleep_calls.append)

    code = reset_audio.main([])

    assert code == 0
    assert before.writes == [("REBOOT", [1])]
    assert before.closed is True
    assert after.closed is True
    assert sleep_calls == [reset_audio.DEFAULT_WAIT_SECONDS]



def test_main_starts_daemon(monkeypatch):
    before = FakeReSpeaker()
    after = FakeReSpeaker([0, 2, 1, 3])

    monkeypatch.setattr("reachy_mini.tools.reset_audio.init_respeaker_usb", lambda: before)
    monkeypatch.setattr("reachy_mini.tools.reset_audio._wait_for_respeaker", lambda *args, **kwargs: after)
    monkeypatch.setattr("reachy_mini.tools.reset_audio.time.sleep", lambda _: None)

    def fake_execvp(file: str, args: list[str]) -> None:
        raise ExecCalled(file, args)

    monkeypatch.setattr("reachy_mini.tools.reset_audio.os.execvp", fake_execvp)

    try:
        reset_audio.main([
            "--start-daemon",
            "--",
            "--no-wake-up-on-start",
            "--log-level",
            "DEBUG",
        ])
    except ExecCalled as exc:
        assert exc.file == "reachy-mini-daemon"
        assert exc.argv == [
            "reachy-mini-daemon",
            "--no-wake-up-on-start",
            "--log-level",
            "DEBUG",
        ]
    else:
        raise AssertionError("os.execvp was not called")



def test_main_returns_1_when_audio_device_missing(monkeypatch):
    monkeypatch.setattr("reachy_mini.tools.reset_audio.init_respeaker_usb", lambda: None)

    assert reset_audio.main([]) == 1



def test_main_returns_1_when_audio_device_does_not_return(monkeypatch):
    before = FakeReSpeaker()

    monkeypatch.setattr("reachy_mini.tools.reset_audio.init_respeaker_usb", lambda: before)
    monkeypatch.setattr("reachy_mini.tools.reset_audio.time.sleep", lambda _: None)

    def fail_wait(*args, **kwargs):
        raise RuntimeError("timed out")

    monkeypatch.setattr("reachy_mini.tools.reset_audio._wait_for_respeaker", fail_wait)

    assert reset_audio.main([]) == 1
