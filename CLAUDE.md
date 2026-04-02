# Claude Code Instructions

Read `AGENTS.md` for full instructions on developing Reachy Mini applications.

## Environment

- **SDK**: v1.6.0 (from source, tracking `upstream/main`)
- **Python**: 3.12 (required — 3.13 not supported)
- **Venv**: `.venv/` (activate with `source .venv/bin/activate`)
- **GStreamer**: 1.28.1, bundled via `gstreamer-python` pip package (no brew needed)
- **Robot**: Reachy Mini Lite (USB at `/dev/cu.usbmodem*`)
- **Audio**: Reachy Mini Audio = sounddevice device 0 (2 in, 2 out)
- **Fork**: `amadad/reachy_mini` — `main` only, synced to upstream

## Daemon

```bash
source .venv/bin/activate && reachy-mini-daemon
```

## Gotchas

- `sounddevice` and `soundfile` are not in `pyproject.toml` but are imported by SDK — install manually after `uv sync`
- Default audio input is MacBook mic (device 2), not Reachy (device 0) — set explicitly in scripts
- The `libgstpython.dylib` warning about `/Library/Frameworks/Python.framework` is cosmetic — GStreamer works fine
