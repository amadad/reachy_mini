# TASKS

Source: review against OpenAI's agent-friendly CLI guide:
https://developers.openai.com/codex/use-cases/agent-friendly-clis

## Goal
Give Reachy Mini a single agent-friendly root CLI for safe inspection, artifact capture, and explicitly approved live robot actions.

## P0

- [x] **REACHY-1: Define a single root CLI for agent workflows**
  - Add a top-level `reachy` command as the primary agent surface
  - Keep existing binaries (`reachy-mini-daemon`, `reachy-mini-app-assistant`, etc.) as compatibility tools
  - Initial command surface should include:
    - `reachy doctor`
    - `reachy daemon status`
    - `reachy state`
    - `reachy devices`
    - `reachy app create/check/publish`
  - **Acceptance:** agent-facing docs can recommend one memorable root command

- [x] **REACHY-2: Ship read-only agent-safe commands first**
  - Prioritize commands for:
    - daemon health/status
    - robot state
    - discovery
    - camera/audio/device inspection
  - Avoid motion as the first implementation focus
  - **Acceptance:** agents can inspect the robot safely without moving hardware

- [x] **REACHY-3: Add stable JSON output contract**
  - Add `--json` to the new root CLI
  - Return structured errors and file paths for generated artifacts
  - **Acceptance:** agent workflows can rely on machine-readable output instead of parsing ad-hoc text

- [x] **REACHY-4: Add install-from-any-folder workflow**
  - Document install via `uv tool install -e .` and/or `pipx install .`
  - Add smoke tests from outside the repo
  - **Acceptance:** `command -v reachy` works outside the repo and `reachy --help` succeeds

## P1

- [x] **REACHY-5: Add explicit approval boundaries for risky actions**
  - Require explicit opt-in for:
    - robot motion
    - motor enable/disable
    - daemon restart
    - app publish
  - Add non-interactive refusal for live actions
  - **Acceptance:** no physical or publish action can happen accidentally in automation

- [x] **REACHY-6: Reposition existing binaries as advanced/internal tools**
  - Keep them working, but move docs toward the new `reachy` root CLI
  - **Acceptance:** new users and agents are not forced to learn a fragmented multi-binary surface first

- [x] **REACHY-7: Refresh companion skill**
  - Update the Reachy skill to prefer the new root CLI
  - Clearly state what is safe to run first and what needs approval
  - Use REST as fallback where appropriate
  - **Acceptance:** skill instructions match the actual CLI and safety model

## P2

- [x] **REACHY-8: Add motion preview / dry-run planning**
  - Add a non-live planning mode for motion commands
  - Example output: target pose, duration, affected subsystems, safety checks
  - **Acceptance:** agents can propose actions before any physical movement occurs

- [x] **REACHY-9: Add artifact-oriented capture commands**
  - Add commands for:
    - camera snapshot to file
    - audio sample to file
    - logs to file
    - diagnostics dump to file
  - **Acceptance:** agents can solve more tasks through files instead of terminal blobs

## Notes from current review
- The repo is strong as an API + AGENTS/skills project
- The main gap is that the CLI surface is fragmented and not JSON-first
- Biggest win is a new root `reachy` CLI
