import io
import json
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock

from reachy_mini.cli import main


class StubRobot:
    def __init__(self, name: str, host: str, port: int, addresses: list[str]):
        self.name = name
        self.host = host
        self.port = port
        self.addresses = addresses
        self.properties = {"robot_name": name}



def run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()



def test_devices_json_output(monkeypatch):
    monkeypatch.setattr(
        "reachy_mini.cli.find_robots",
        lambda timeout=1.5: [StubRobot("mini", "mini.local", 8000, ["192.168.1.10"])],
    )

    code, stdout, _ = run_cli(["devices", "--json", "--timeout", "0.1"])

    assert code == 0
    payload = json.loads(stdout)
    assert payload["command"] == "devices"
    assert payload["data"]["robots"][0]["name"] == "mini"



def test_doctor_json_output(monkeypatch):
    monkeypatch.setattr(
        "reachy_mini.cli.gather_doctor_data",
        lambda host, port, timeout: {
            "configured_host": host,
            "configured_port": port,
            "reachable": True,
            "daemon_status": {"robot_name": "mini"},
            "state": {},
            "media": {},
            "camera": {},
            "discovered_robots": [],
            "warnings": [],
            "errors": [],
        },
    )

    code, stdout, _ = run_cli(["doctor", "--host", "robot", "--port", "9000", "--json"])

    assert code == 0
    payload = json.loads(stdout)
    assert payload["command"] == "doctor"
    assert payload["data"]["configured_host"] == "robot"
    assert payload["data"]["configured_port"] == 9000



def test_daemon_restart_requires_live_flag(monkeypatch):
    post_mock = MagicMock(return_value={"job_id": "123"})
    monkeypatch.setattr("reachy_mini.cli.daemon_post", post_mock)

    code, _, stderr = run_cli(["daemon", "restart", "--host", "robot"])

    assert code == 1
    assert "--live" in stderr
    post_mock.assert_not_called()



def test_daemon_restart_json(monkeypatch):
    monkeypatch.setattr("reachy_mini.cli.daemon_post", lambda host, port, path, timeout=5.0: {"job_id": "123"})

    code, stdout, _ = run_cli(["daemon", "restart", "--host", "robot", "--live", "--json"])

    assert code == 0
    payload = json.loads(stdout)
    assert payload["command"] == "daemon-restart"
    assert payload["data"]["result"]["job_id"] == "123"



def test_motion_preview_json():
    code, stdout, _ = run_cli([
        "motion",
        "preview",
        "--head-pitch",
        "15",
        "--body-yaw",
        "30",
        "--json",
    ])

    assert code == 0
    payload = json.loads(stdout)
    assert payload["command"] == "motion-preview"
    assert payload["data"]["head"]["pitch_deg"] == 15.0
    assert payload["data"]["body"]["yaw_deg"] == 30.0



def test_app_publish_requires_live(monkeypatch):
    publish_mock = MagicMock()
    monkeypatch.setattr("reachy_mini.cli.assistant.publish", publish_mock)

    code, _, stderr = run_cli(["app", "publish", "/tmp/app", "ship it"])

    assert code == 1
    assert "--live" in stderr
    publish_mock.assert_not_called()
