# ruff: noqa: D103
"""Local voice stack self-check for Reachy Mini.

This validates the software pieces needed for a local feedback loop:
- KittenTTS text-to-speech
- faster-whisper speech-to-text
- openWakeWord asset bootstrap and model loading
- optional speaker playback via sounddevice
- optional microphone recording probe via sounddevice

Typical usage:
    uv sync --group voice
    uv run reachy-mini-local-voice-selfcheck
    uv run reachy-mini-local-voice-selfcheck --record-seconds 3
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

VOICE_GROUP_HINT = "Install missing voice deps with: uv sync --group voice"
DEFAULT_TEXT = "Hello from Reachy. This is a local voice self-check."
DEFAULT_WAKEWORD = "hey_jarvis"
DEFAULT_DEVICE_NAME = "Reachy Mini Audio"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "reachy_mini" / "openwakeword"


def _require_voice_deps() -> dict[str, Any]:
    try:
        import sounddevice as sd
        import soundfile as sf
        from faster_whisper import WhisperModel
        from kittentts import KittenTTS
        from openwakeword import Model
        from openwakeword.utils import download_models
    except ImportError as exc:
        raise RuntimeError(f"{exc}. {VOICE_GROUP_HINT}") from exc

    return {
        "sd": sd,
        "sf": sf,
        "WhisperModel": WhisperModel,
        "KittenTTS": KittenTTS,
        "WakewordModel": Model,
        "download_models": download_models,
    }


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())


def _rms_and_peak(audio: npt.NDArray[np.float32]) -> tuple[float, float]:
    flat = np.asarray(audio, dtype=np.float64).reshape(-1)
    if flat.size == 0:
        return 0.0, 0.0
    return float(np.sqrt(np.mean(np.square(flat)))), float(np.max(np.abs(flat)))


def _list_devices(sd: Any) -> list[dict[str, Any]]:
    return [dict(device) for device in sd.query_devices()]


def _print_devices(devices: list[dict[str, Any]]) -> None:
    print("Audio devices:")
    for idx, device in enumerate(devices):
        print(
            f"  [{idx}] {device['name']} | in={device['max_input_channels']} "
            f"out={device['max_output_channels']} sr={int(device['default_samplerate'])}"
        )


def _resolve_device(
    devices: list[dict[str, Any]], selector: str | None, *, want_input: bool
) -> int | None:
    needed_key = "max_input_channels" if want_input else "max_output_channels"

    def usable(device: dict[str, Any]) -> bool:
        return int(device.get(needed_key, 0)) > 0

    if selector is None:
        selector = DEFAULT_DEVICE_NAME

    selector = selector.strip()
    if selector.isdigit():
        idx = int(selector)
        if idx < 0 or idx >= len(devices) or not usable(devices[idx]):
            raise ValueError(f"Device {idx} is not a valid {'input' if want_input else 'output'} device")
        return idx

    lowered = selector.lower()
    for idx, device in enumerate(devices):
        if usable(device) and lowered in str(device["name"]).lower():
            return idx
    return None


def _ensure_openwakeword_assets(download_models: Any, cache_dir: Path, wakeword: str) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    required = {
        "embedding": cache_dir / "embedding_model.onnx",
        "melspectrogram": cache_dir / "melspectrogram.onnx",
        "wakeword": cache_dir / f"{wakeword}_v0.1.onnx",
    }
    if not all(path.exists() for path in required.values()):
        print(f"Bootstrapping openWakeWord assets into {cache_dir}...")
        download_models([wakeword], target_directory=str(cache_dir))
    return required


def _generate_tts(KittenTTS: Any, text: str, voice: str, speed: float) -> tuple[npt.NDArray[np.float32], int]:
    model = KittenTTS("KittenML/kitten-tts-nano-0.8", backend="cpu")
    audio = np.asarray(model.generate(text, voice=voice, speed=speed), dtype=np.float32)
    return audio, 24000


def _play_audio(sd: Any, audio: npt.NDArray[np.float32], samplerate: int, device: int | None) -> None:
    if device is None:
        print("No output device selected, skipping playback.")
        return
    sd.play(audio, samplerate, device=device)
    sd.wait()


def _write_wav(sf: Any, path: Path, audio: npt.NDArray[np.float32], samplerate: int) -> None:
    sf.write(str(path), audio, samplerate)


def _transcribe(WhisperModel: Any, wav_path: Path) -> str:
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(wav_path), language="en")
    return " ".join(segment.text.strip() for segment in segments).strip()


def _record(sd: Any, sf: Any, seconds: float, device: int, output_path: Path) -> tuple[float, float, int]:
    device_info = sd.query_devices(device)
    samplerate = int(device_info["default_samplerate"])
    audio = sd.rec(
        int(seconds * samplerate),
        samplerate=samplerate,
        channels=1,
        device=device,
        dtype="float32",
    )
    sd.wait()
    _write_wav(sf, output_path, audio, samplerate)
    rms, peak = _rms_and_peak(audio)
    return rms, peak, samplerate


def _load_wakeword_model(WakewordModel: Any, assets: dict[str, Path], wakeword: str) -> Any:
    return WakewordModel(
        wakeword_models=[str(assets["wakeword"])],
        inference_framework="onnx",
        melspec_model_path=str(assets["melspectrogram"]),
        embedding_model_path=str(assets["embedding"]),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reachy local voice self-check")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text to synthesize for the TTS/STT check")
    parser.add_argument("--voice", default="expr-voice-5-m", help="KittenTTS voice")
    parser.add_argument("--speed", type=float, default=1.25, help="KittenTTS speed")
    parser.add_argument(
        "--output-device",
        default=DEFAULT_DEVICE_NAME,
        help="Output device index or case-insensitive name fragment",
    )
    parser.add_argument(
        "--input-device",
        default=DEFAULT_DEVICE_NAME,
        help="Input device index or case-insensitive name fragment",
    )
    parser.add_argument(
        "--no-playback",
        action="store_true",
        help="Skip speaker playback after TTS generation",
    )
    parser.add_argument(
        "--record-seconds",
        type=float,
        default=0.0,
        help="If > 0, record from the selected input device and attempt STT on the capture",
    )
    parser.add_argument(
        "--wakeword",
        default=DEFAULT_WAKEWORD,
        choices=[DEFAULT_WAKEWORD],
        help="Wakeword model to bootstrap and load",
    )
    parser.add_argument(
        "--wakeword-cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Cache directory for openWakeWord assets",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        deps = _require_voice_deps()
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    sd = deps["sd"]
    sf = deps["sf"]
    devices = _list_devices(sd)
    _print_devices(devices)

    if args.list_devices:
        return 0

    try:
        output_device = _resolve_device(devices, args.output_device, want_input=False)
        input_device = _resolve_device(devices, args.input_device, want_input=True)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if output_device is not None:
        print(f"Using output device [{output_device}] {devices[output_device]['name']}")
    else:
        print("WARN: no matching output device found")

    if input_device is not None:
        print(f"Using input device [{input_device}] {devices[input_device]['name']}")
    else:
        print("WARN: no matching input device found")

    try:
        assets = _ensure_openwakeword_assets(
            deps["download_models"], args.wakeword_cache_dir, args.wakeword
        )
        wake_model = _load_wakeword_model(deps["WakewordModel"], assets, args.wakeword)
        print(f"PASS: openWakeWord ready ({', '.join(wake_model.models.keys())})")
    except Exception as exc:
        print(f"FAIL: openWakeWord bootstrap/load failed: {exc}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="reachy-voice-selfcheck-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        tts_wav = tmpdir_path / "tts.wav"

        try:
            tts_audio, tts_sr = _generate_tts(
                deps["KittenTTS"], args.text, args.voice, args.speed
            )
            _write_wav(sf, tts_wav, tts_audio, tts_sr)
            tts_rms, tts_peak = _rms_and_peak(tts_audio)
            print(
                f"PASS: KittenTTS generated {tts_wav.name} "
                f"(samples={tts_audio.shape[0]}, sr={tts_sr}, rms={tts_rms:.4f}, peak={tts_peak:.4f})"
            )
        except Exception as exc:
            print(f"FAIL: KittenTTS generation failed: {exc}", file=sys.stderr)
            return 1

        if not args.no_playback:
            try:
                _play_audio(sd, tts_audio, tts_sr, output_device)
                print("PASS: speaker playback completed")
            except Exception as exc:
                print(f"FAIL: speaker playback failed: {exc}", file=sys.stderr)
                return 1

        try:
            transcript = _transcribe(deps["WhisperModel"], tts_wav)
            print(f"PASS: faster-whisper transcript: {transcript!r}")
            if not transcript:
                print("FAIL: faster-whisper returned empty text", file=sys.stderr)
                return 1
            if _normalize_text(transcript) != _normalize_text(args.text):
                print("WARN: transcript differs from prompt (common with tiny wording drift)")
        except Exception as exc:
            print(f"FAIL: faster-whisper transcription failed: {exc}", file=sys.stderr)
            return 1

        if args.record_seconds > 0:
            if input_device is None:
                print("FAIL: no input device available for live mic probe", file=sys.stderr)
                return 1

            mic_wav = tmpdir_path / "mic.wav"
            print(
                f"Recording {args.record_seconds:.1f}s from [{input_device}] "
                f"{devices[input_device]['name']}..."
            )
            try:
                rms, peak, samplerate = _record(
                    sd, sf, args.record_seconds, input_device, mic_wav
                )
                print(
                    f"Mic probe saved {mic_wav.name} "
                    f"(sr={samplerate}, rms={rms:.6f}, peak={peak:.6f})"
                )
            except Exception as exc:
                print(f"FAIL: mic recording failed: {exc}", file=sys.stderr)
                return 1

            if rms <= 1e-6 and peak <= 1e-6:
                print(
                    "FAIL: mic recording was all zeros. The local voice software stack is ready, "
                    "but live input is still blocked.",
                    file=sys.stderr,
                )
                return 1

            try:
                mic_text = _transcribe(deps["WhisperModel"], mic_wav)
                print(f"PASS: live mic transcript: {mic_text!r}")
            except Exception as exc:
                print(f"FAIL: live mic transcription failed: {exc}", file=sys.stderr)
                return 1

    print("DONE: local voice software stack is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
