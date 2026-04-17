#!/usr/bin/env python3
# ruff: noqa: D103
"""Smoke-test the installed Reachy root CLI from outside the source tree."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"Command failed ({result.returncode}): {' '.join(command)}\n\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )
    return result



def main() -> int:
    cli = shutil.which("reachy")
    if not cli:
        raise SystemExit("`reachy` is not on PATH. Install it first with `uv tool install -e .` or `pipx install .`.")

    with tempfile.TemporaryDirectory(prefix="reachy-agent-cli-") as temp_dir:
        cwd = Path(temp_dir)

        run([cli, "--help"], cwd=cwd)

        devices = json.loads(run([cli, "devices", "--json", "--timeout", "0.1"], cwd=cwd).stdout)
        assert devices["success"] is True

        run([cli, "app", "create", "demo_agent_app", str(cwd)], cwd=cwd)
        app_dir = cwd / "demo_agent_app"
        assert app_dir.exists()

        run([cli, "app", "check", str(app_dir)], cwd=cwd)

        print("reachy agent CLI verification passed")
        print(f"verified from: {cwd}")
        print(f"created app: {app_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
