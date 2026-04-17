"""Reset the Reachy Mini audio XMOS chip and optionally start the daemon.

This is useful when the Reachy Mini Lite microphone returns all-zero audio
samples after USB reconnect, suspend/resume, or macOS sleep/wake.

Typical usage:
    uv run reachy-mini-reset-audio
    uv run reachy-mini-reset-audio --start-daemon -- --no-wake-up-on-start
"""

from __future__ import annotations

import argparse
import os
import shlex
import time
from typing import Any, Callable

from reachy_mini.media.audio_control_utils import init_respeaker_usb

DEFAULT_WAIT_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.25


def _format_version(version: Any) -> str:
    if isinstance(version, (list, tuple)) and version:
        return ".".join(str(part) for part in version)
    return str(version)


def _strip_remainder_separator(args: list[str]) -> list[str]:
    if args and args[0] == "--":
        return args[1:]
    return args


def _wait_for_respeaker(
    finder: Callable[[], Any | None],
    *,
    timeout_seconds: float,
    sleep_fn: Callable[[float], None],
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        device = finder()
        if device is not None:
            return device
        sleep_fn(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        "Reachy Mini Audio did not come back after XMOS reboot. "
        "Try unplugging/replugging USB or power-cycling the robot."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset Reachy Mini Audio XMOS and optionally start the daemon"
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=DEFAULT_WAIT_SECONDS,
        help="Initial wait after sending the XMOS reboot command (default: 5.0)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="How long to wait for the audio device to reappear (default: 15.0)",
    )
    parser.add_argument(
        "--start-daemon",
        action="store_true",
        help="Exec reachy-mini-daemon after the reset completes",
    )
    parser.add_argument(
        "daemon_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to reachy-mini-daemon after '--'",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    respeaker = init_respeaker_usb()
    if respeaker is None:
        print("FAIL: Reachy Mini Audio USB control device not found")
        return 1

    version_before = respeaker.read("VERSION")
    print(f"XMOS version before reset: {_format_version(version_before)}")
    print("Sending XMOS reboot...")
    respeaker.write("REBOOT", [1])
    respeaker.close()

    time.sleep(args.wait_seconds)

    try:
        respeaker = _wait_for_respeaker(
            init_respeaker_usb,
            timeout_seconds=args.timeout_seconds,
            sleep_fn=time.sleep,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    version_after = respeaker.read("VERSION")
    respeaker.close()
    print(f"Reachy Mini Audio is back: {_format_version(version_after)}")

    if not args.start_daemon:
        print("DONE: XMOS audio reset completed")
        return 0

    daemon_args = _strip_remainder_separator(list(args.daemon_args))
    command = ["reachy-mini-daemon", *daemon_args]
    print(f"Starting daemon: {shlex.join(command)}")
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
