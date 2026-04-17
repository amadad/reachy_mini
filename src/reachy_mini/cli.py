"""Agent-friendly Reachy Mini root CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import wave
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from rich.console import Console

from reachy_mini import __version__
from reachy_mini.apps import assistant
from reachy_mini.utils.discovery import find_robots

DEFAULT_HOST_ENV = "REACHY_HOST"
DEFAULT_PORT_ENV = "REACHY_PORT"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8000

TOP_LEVEL_DESCRIPTION = """Agent-friendly Reachy Mini CLI.

Use when you need to discover a Reachy Mini, inspect daemon or robot state,
capture diagnostics or media artifacts, preview motion plans, or manage apps
with explicit `--live` approval for risky actions.
"""

TOP_LEVEL_EPILOG = """Examples:
  reachy devices --json
  reachy doctor --json
  reachy daemon status --json
  reachy capture diagnostics --output ./reachy-diagnostics.json --json
  reachy motion preview --head-pitch 15 --body-yaw 30 --json
  reachy app publish ./my_app \"Initial publish\" --live
"""


def build_response(
    command: str,
    *,
    success: bool = True,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command": command,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "success": success,
        "data": data,
        "error": error,
    }


class CLIError(Exception):
    """Expected CLI failure with a user-safe message."""



def print_json_response(
    command: str,
    *,
    success: bool = True,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    print(json.dumps(build_response(command, success=success, data=data, error=error), indent=2))



def resolve_host(value: str | None) -> str:
    return value or os.getenv(DEFAULT_HOST_ENV, DEFAULT_HOST)



def resolve_port(value: int | None) -> int:
    if value is not None:
        return value
    raw = os.getenv(DEFAULT_PORT_ENV)
    if raw:
        return int(raw)
    return DEFAULT_PORT



def base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"



def daemon_get(host: str, port: int, path: str, *, timeout: float = 5.0) -> dict[str, Any]:
    response = requests.get(f"{base_url(host, port)}{path}", timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return {"value": payload}



def daemon_post(host: str, port: int, path: str, *, timeout: float = 5.0) -> dict[str, Any]:
    response = requests.post(f"{base_url(host, port)}{path}", timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return {"value": payload}



def require_live_action(action: str, *, live: bool, non_interactive: bool) -> None:
    if not live:
        raise CLIError(
            f"{action} is a live action. Re-run with --live after explicit user approval."
        )
    if non_interactive:
        raise CLIError(f"--non-interactive cannot be combined with live action '{action}'.")



def json_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))



def emit(command: str, args: argparse.Namespace, data: dict[str, Any]) -> None:
    if json_requested(args):
        print_json_response(command, data=data)
    else:
        for key, value in data.items():
            print(f"{key}: {value}")



def robot_to_dict(robot) -> dict[str, Any]:
    return {
        "name": robot.name,
        "host": robot.host,
        "port": robot.port,
        "addresses": list(robot.addresses),
        "properties": dict(robot.properties),
    }



def run_devices(args: argparse.Namespace) -> int:
    robots = find_robots(timeout=args.timeout)
    data = {"robots": [robot_to_dict(robot) for robot in robots]}
    if json_requested(args):
        print_json_response("devices", data=data)
        return 0

    if not robots:
        print("No Reachy Mini robots discovered.")
        return 0

    print(f"Found {len(robots)} robot(s):")
    for robot in robots:
        address = ", ".join(robot.addresses) or "<no-address>"
        print(f"- {robot.name}: {address} ({robot.host}:{robot.port})")
    return 0



def gather_doctor_data(host: str, port: int, *, timeout: float) -> dict[str, Any]:
    data: dict[str, Any] = {
        "configured_host": host,
        "configured_port": port,
        "reachable": False,
        "daemon_status": None,
        "state": None,
        "media": None,
        "camera": None,
        "discovered_robots": [robot_to_dict(robot) for robot in find_robots(timeout=min(timeout, 2.0))],
        "warnings": [],
        "errors": [],
    }

    try:
        data["daemon_status"] = daemon_get(host, port, "/api/daemon/status", timeout=timeout)
        data["reachable"] = True
    except requests.RequestException as exc:
        data["errors"].append(str(exc))
        if host == DEFAULT_HOST:
            data["warnings"].append(
                "Daemon did not respond on localhost. Try `reachy devices --json` or set REACHY_HOST/--host."
            )
        return data

    for name, path in (
        ("state", "/api/state/full"),
        ("media", "/api/media/status"),
        ("camera", "/api/camera/specs"),
    ):
        try:
            data[name] = daemon_get(host, port, path, timeout=timeout)
        except requests.RequestException as exc:
            data["warnings"].append(f"{name}: {exc}")

    return data



def run_doctor(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    data = gather_doctor_data(host, port, timeout=args.timeout)

    if json_requested(args):
        print_json_response("doctor", data=data)
        return 0 if data["reachable"] else 1

    print("Reachy doctor")
    print(f"- target: {host}:{port}")
    print(f"- reachable: {'yes' if data['reachable'] else 'no'}")
    if data["daemon_status"]:
        print(f"- daemon robot: {data['daemon_status'].get('robot_name', 'unknown')}")
        print(f"- no_media: {data['daemon_status'].get('no_media')}")
    if data["warnings"]:
        print("Warnings:")
        for warning in data["warnings"]:
            print(f"- {warning}")
    if data["errors"]:
        print("Errors:")
        for error in data["errors"]:
            print(f"- {error}")
        return 1
    return 0



def run_daemon_status(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    status = daemon_get(host, port, "/api/daemon/status", timeout=args.timeout)
    if json_requested(args):
        print_json_response("daemon-status", data={"host": host, "port": port, "status": status})
    else:
        print(f"Reachy daemon status ({host}:{port})")
        for key, value in status.items():
            print(f"- {key}: {value}")
    return 0



def run_daemon_restart(args: argparse.Namespace) -> int:
    require_live_action("daemon restart", live=args.live, non_interactive=args.non_interactive)
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    result = daemon_post(host, port, "/api/daemon/restart", timeout=args.timeout)
    if json_requested(args):
        print_json_response("daemon-restart", data={"host": host, "port": port, "result": result})
    else:
        print(f"Restart requested for {host}:{port}")
        print(result)
    return 0



def run_state(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    state = daemon_get(host, port, "/api/state/full", timeout=args.timeout)
    if json_requested(args):
        print_json_response("state", data={"host": host, "port": port, "state": state})
    else:
        print(f"Reachy state ({host}:{port})")
        print(json.dumps(state, indent=2, default=str))
    return 0



def run_camera_specs(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    camera = daemon_get(host, port, "/api/camera/specs", timeout=args.timeout)
    if json_requested(args):
        print_json_response("camera-specs", data={"host": host, "port": port, "camera": camera})
    else:
        print(f"Camera specs ({host}:{port})")
        print(json.dumps(camera, indent=2))
    return 0



def build_motion_preview(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[str] = []
    if args.head_pitch is not None and not -40 <= args.head_pitch <= 40:
        warnings.append("head pitch is outside the documented safe range [-40, 40] degrees")
    if args.head_roll is not None and not -40 <= args.head_roll <= 40:
        warnings.append("head roll is outside the documented safe range [-40, 40] degrees")
    if args.head_yaw is not None and not -180 <= args.head_yaw <= 180:
        warnings.append("head yaw is outside the documented safe range [-180, 180] degrees")
    if args.body_yaw is not None and not -160 <= args.body_yaw <= 160:
        warnings.append("body yaw is outside the documented safe range [-160, 160] degrees")

    affected = []
    if any(value is not None for value in (args.head_pitch, args.head_yaw, args.head_roll)):
        affected.append("head")
    if args.body_yaw is not None:
        affected.append("body")
    if any(value is not None for value in (args.left_antenna, args.right_antenna)):
        affected.append("antennas")

    return {
        "duration_seconds": args.duration,
        "affected_subsystems": affected,
        "head": {
            "pitch_deg": args.head_pitch,
            "yaw_deg": args.head_yaw,
            "roll_deg": args.head_roll,
        },
        "body": {"yaw_deg": args.body_yaw},
        "antennas": {
            "left_rad": args.left_antenna,
            "right_rad": args.right_antenna,
        },
        "warnings": warnings,
    }



def run_motion_preview(args: argparse.Namespace) -> int:
    preview = build_motion_preview(args)
    if json_requested(args):
        print_json_response("motion-preview", data=preview)
    else:
        print("Reachy motion preview")
        print(json.dumps(preview, indent=2))
    return 0



def _sdk_connection_mode(host: str) -> str:
    return "localhost_only" if host in {"localhost", "127.0.0.1"} else "network"



def save_ppm(path: Path, frame) -> None:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    with path.open("wb") as fh:
        fh.write(f"P6\n{width} {height}\n255\n".encode())
        fh.write(frame.astype("uint8").tobytes())



def save_wav(path: Path, audio) -> None:  # type: ignore[no-untyped-def]
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.asarray(audio)
    if pcm.ndim == 1:
        channels = 1
    else:
        channels = pcm.shape[1]
    pcm = np.clip(pcm, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm16.tobytes())



def run_capture_diagnostics(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    output = Path(args.output).expanduser().resolve()
    report = gather_doctor_data(host, port, timeout=args.timeout)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str))
    payload = {"output_path": str(output), "reachable": report["reachable"]}
    if json_requested(args):
        print_json_response("capture-diagnostics", data=payload)
    else:
        print(f"Diagnostics written to {output}")
    return 0 if report["reachable"] else 1



def run_capture_frame(args: argparse.Namespace) -> int:
    from reachy_mini import ReachyMini

    host = resolve_host(args.host)
    port = resolve_port(args.port)
    output = Path(args.output).expanduser().resolve()
    with ReachyMini(host=host, port=port, connection_mode=_sdk_connection_mode(host)) as mini:
        frame = mini.media.get_frame()
        if frame is None:
            raise CLIError("No camera frame available from the daemon.")
        save_ppm(output, frame)

    payload = {"output_path": str(output), "format": "ppm"}
    if json_requested(args):
        print_json_response("capture-frame", data=payload)
    else:
        print(f"Camera frame written to {output}")
    return 0



def run_capture_audio(args: argparse.Namespace) -> int:
    from reachy_mini import ReachyMini

    host = resolve_host(args.host)
    port = resolve_port(args.port)
    output = Path(args.output).expanduser().resolve()
    with ReachyMini(host=host, port=port, connection_mode=_sdk_connection_mode(host)) as mini:
        audio = mini.media.get_audio_sample()
        if audio is None:
            raise CLIError("No audio sample available from the daemon.")
        save_wav(output, audio)

    payload = {"output_path": str(output), "format": "wav"}
    if json_requested(args):
        print_json_response("capture-audio", data=payload)
    else:
        print(f"Audio sample written to {output}")
    return 0



def capture_local_logs(lines: int) -> list[str]:
    if not shutil.which("journalctl"):
        raise CLIError("journalctl is not available locally and remote log streaming is unsupported here.")
    result = subprocess.run(
        [
            "journalctl",
            "-u",
            "reachy-mini-daemon",
            "-n",
            str(lines),
            "--output",
            "short-iso",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


async def capture_remote_logs(host: str, port: int, lines: int, timeout: float) -> list[str]:
    import websockets

    uri = f"ws://{host}:{port}/api/logs/ws/daemon"
    collected: list[str] = []
    async with websockets.connect(uri) as websocket:
        while len(collected) < lines:
            message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            if message:
                collected.append(str(message))
    return collected



def run_capture_logs(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    output = Path(args.output).expanduser().resolve()

    if host in {"localhost", "127.0.0.1"}:
        lines = capture_local_logs(args.lines)
    else:
        lines = asyncio.run(capture_remote_logs(host, port, args.lines, args.timeout))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")
    payload = {"output_path": str(output), "line_count": len(lines)}
    if json_requested(args):
        print_json_response("capture-logs", data=payload)
    else:
        print(f"Logs written to {output}")
    return 0



def run_app_create(args: argparse.Namespace) -> int:
    console = Console()
    created = assistant.create(console, app_name=args.app_name, app_path=Path(args.path))
    if args.publish:
        require_live_action("app publish", live=args.live, non_interactive=args.non_interactive)
        assistant.publish(
            console,
            app_path=str(created),
            commit_message=args.commit_message,
            official=False,
            no_check=False,
            private=args.private,
        )
    return 0



def run_app_check(args: argparse.Namespace) -> int:
    console = Console()
    assistant.check(console, app_path=str(Path(args.app_path)))
    return 0



def run_app_publish(args: argparse.Namespace) -> int:
    require_live_action("app publish", live=args.live, non_interactive=args.non_interactive)
    console = Console()
    private: bool | None
    if args.private:
        private = True
    elif args.public:
        private = False
    else:
        private = None
    assistant.publish(
        console,
        app_path=str(Path(args.app_path)),
        commit_message=args.commit_message,
        official=args.official,
        no_check=args.nocheck,
        private=private,
    )
    return 0



def add_connection_args(parser: argparse.ArgumentParser, *, allow_json: bool = True) -> None:
    parser.add_argument("--host", help=f"Daemon host (default: ${DEFAULT_HOST_ENV} or {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, help=f"Daemon port (default: ${DEFAULT_PORT_ENV} or {DEFAULT_PORT})")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    if allow_json:
        parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reachy",
        description=TOP_LEVEL_DESCRIPTION,
        epilog=TOP_LEVEL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", "-V", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    devices_parser = subparsers.add_parser("devices", help="Discover Reachy Mini robots on the network")
    devices_parser.add_argument("--timeout", type=float, default=1.5, help="Discovery timeout in seconds")
    devices_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    devices_parser.set_defaults(handler=run_devices)

    doctor_parser = subparsers.add_parser("doctor", help="Check daemon connectivity and runtime health")
    add_connection_args(doctor_parser)
    doctor_parser.set_defaults(handler=run_doctor)

    daemon_parser = subparsers.add_parser("daemon", help="Daemon operations")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)

    daemon_status_parser = daemon_subparsers.add_parser("status", help="Get daemon status")
    add_connection_args(daemon_status_parser)
    daemon_status_parser.set_defaults(handler=run_daemon_status)

    daemon_restart_parser = daemon_subparsers.add_parser("restart", help="Restart the daemon")
    add_connection_args(daemon_restart_parser)
    daemon_restart_parser.add_argument("--live", action="store_true", help="Confirm a live action")
    daemon_restart_parser.add_argument("--non-interactive", action="store_true", help="Fail instead of performing a live action")
    daemon_restart_parser.set_defaults(handler=run_daemon_restart)

    state_parser = subparsers.add_parser("state", help="Get the full robot state")
    add_connection_args(state_parser)
    state_parser.set_defaults(handler=run_state)

    motion_parser = subparsers.add_parser("motion", help="Motion planning commands")
    motion_subparsers = motion_parser.add_subparsers(dest="motion_command", required=True)
    motion_preview_parser = motion_subparsers.add_parser("preview", help="Preview a motion plan without moving the robot")
    motion_preview_parser.add_argument("--head-pitch", type=float, help="Head pitch in degrees")
    motion_preview_parser.add_argument("--head-yaw", type=float, help="Head yaw in degrees")
    motion_preview_parser.add_argument("--head-roll", type=float, help="Head roll in degrees")
    motion_preview_parser.add_argument("--body-yaw", type=float, help="Body yaw in degrees")
    motion_preview_parser.add_argument("--left-antenna", type=float, help="Left antenna target in radians")
    motion_preview_parser.add_argument("--right-antenna", type=float, help="Right antenna target in radians")
    motion_preview_parser.add_argument("--duration", type=float, default=1.0, help="Planned move duration in seconds")
    motion_preview_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    motion_preview_parser.set_defaults(handler=run_motion_preview)

    camera_parser = subparsers.add_parser("camera", help="Camera inspection commands")
    camera_subparsers = camera_parser.add_subparsers(dest="camera_command", required=True)
    camera_specs_parser = camera_subparsers.add_parser("specs", help="Get detected camera specifications")
    add_connection_args(camera_specs_parser)
    camera_specs_parser.set_defaults(handler=run_camera_specs)

    capture_parser = subparsers.add_parser("capture", help="Save diagnostics and media artifacts to files")
    capture_subparsers = capture_parser.add_subparsers(dest="capture_command", required=True)

    capture_diag_parser = capture_subparsers.add_parser("diagnostics", help="Write a diagnostics JSON file")
    add_connection_args(capture_diag_parser)
    capture_diag_parser.add_argument("--output", required=True, help="Output JSON path")
    capture_diag_parser.set_defaults(handler=run_capture_diagnostics)

    capture_frame_parser = capture_subparsers.add_parser("frame", help="Capture one camera frame to a PPM file")
    add_connection_args(capture_frame_parser)
    capture_frame_parser.add_argument("--output", required=True, help="Output image path (.ppm)")
    capture_frame_parser.set_defaults(handler=run_capture_frame)

    capture_audio_parser = capture_subparsers.add_parser("audio", help="Capture one audio sample to a WAV file")
    add_connection_args(capture_audio_parser)
    capture_audio_parser.add_argument("--output", required=True, help="Output audio path (.wav)")
    capture_audio_parser.set_defaults(handler=run_capture_audio)

    capture_logs_parser = capture_subparsers.add_parser("logs", help="Save daemon logs to a file")
    add_connection_args(capture_logs_parser)
    capture_logs_parser.add_argument("--output", required=True, help="Output log path")
    capture_logs_parser.add_argument("--lines", type=int, default=100, help="How many lines to collect")
    capture_logs_parser.set_defaults(handler=run_capture_logs)

    app_parser = subparsers.add_parser("app", help="App assistant workflows")
    app_subparsers = app_parser.add_subparsers(dest="app_command", required=True)

    app_create_parser = app_subparsers.add_parser("create", help="Create a new app project")
    app_create_parser.add_argument("app_name", help="App name")
    app_create_parser.add_argument("path", help="Directory where the app should be created")
    app_create_parser.add_argument("--publish", action="store_true", help="Publish immediately after creation")
    app_create_parser.add_argument("--commit-message", default="Initial commit", help="Commit message if publishing")
    app_create_parser.add_argument("--private", action="store_true", help="Publish the app as private")
    app_create_parser.add_argument("--live", action="store_true", help="Confirm a live action")
    app_create_parser.add_argument("--non-interactive", action="store_true", help="Fail instead of performing a live action")
    app_create_parser.set_defaults(handler=run_app_create)

    app_check_parser = app_subparsers.add_parser("check", help="Check an app project")
    app_check_parser.add_argument("app_path", help="Path to the app project")
    app_check_parser.set_defaults(handler=run_app_check)

    app_publish_parser = app_subparsers.add_parser("publish", help="Publish an app project")
    app_publish_parser.add_argument("app_path", help="Path to the app project")
    app_publish_parser.add_argument("commit_message", help="Commit message")
    app_publish_parser.add_argument("--official", action="store_true", help="Request official app review")
    app_publish_parser.add_argument("--nocheck", action="store_true", help="Skip validation before publish")
    app_publish_parser.add_argument("--private", action="store_true", help="Make the published app private")
    app_publish_parser.add_argument("--public", action="store_true", help="Make the published app public")
    app_publish_parser.add_argument("--live", action="store_true", help="Confirm a live action")
    app_publish_parser.add_argument("--non-interactive", action="store_true", help="Fail instead of performing a live action")
    app_publish_parser.set_defaults(handler=run_app_publish)

    return parser



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return int(args.handler(args))
    except CLIError as exc:
        if json_requested(args):
            print_json_response(
                args.command,
                success=False,
                error={"type": "CLIError", "message": str(exc)},
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        if json_requested(args):
            print_json_response(
                args.command,
                success=False,
                error={"type": "RequestError", "message": str(exc)},
            )
        else:
            print(f"Request error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        with suppress(BrokenPipeError):
            print("Interrupted.", file=sys.stderr)
        return 1
    except Exception as exc:
        if json_requested(args):
            print_json_response(
                args.command,
                success=False,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        else:
            print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
