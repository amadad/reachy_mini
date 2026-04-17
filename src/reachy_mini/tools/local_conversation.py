"""Local voice conversation loop for Reachy Mini.

Fully offline pipeline:
  Wake word (openWakeWord "hey Jarvis")
  → STT (faster-whisper)
  → LLM (llama-server via OpenAI-compatible API)
  → TTS (KittenTTS)
  → Reachy Mini speaker + expressive gestures

Prerequisites:
    uv sync --group voice
    llama-server --model ~/.cache/reachy_mini/models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8081

Usage:
    uv run reachy-mini-local-conversation
    uv run reachy-mini-local-conversation --no-robot   # audio only, no robot
    uv run reachy-mini-local-conversation --input-device 2  # use MacBook mic
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

WAKEWORD = "hey_jarvis"
WAKEWORD_CACHE = Path.home() / ".cache" / "reachy_mini" / "openwakeword"
DEFAULT_LLAMA_URL = "http://localhost:8081"
SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 24000
WAKEWORD_CHUNK_MS = 80  # openWakeWord expects 80ms chunks at 16kHz
WAKEWORD_CHUNK_SAMPLES = int(SAMPLE_RATE * WAKEWORD_CHUNK_MS / 1000)
SILENCE_THRESHOLD = 0.008
SILENCE_DURATION = 1.5  # seconds of silence to stop recording
MAX_RECORD_SECONDS = 15.0
SYSTEM_PROMPT = (
    "You are Jarvis, a friendly and helpful assistant living inside a small robot called Reachy Mini. "
    "Keep your responses concise — one to three sentences max. Be warm, witty, and conversational. "
    "You can hear and speak but cannot see."
)


def _import_deps() -> dict[str, Any]:
    try:
        import sounddevice as sd
        import soundfile as sf
        from faster_whisper import WhisperModel
        from kittentts import KittenTTS
        from openwakeword import Model as WakewordModel
        from openwakeword.utils import download_models
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: uv sync --group voice", file=sys.stderr)
        sys.exit(2)
    return {
        "sd": sd, "sf": sf,
        "WhisperModel": WhisperModel,
        "KittenTTS": KittenTTS,
        "WakewordModel": WakewordModel,
        "download_models": download_models,
    }


def _resolve_device(sd: Any, selector: str | None, *, want_input: bool) -> int | None:
    key = "max_input_channels" if want_input else "max_output_channels"
    devices = [dict(d) for d in sd.query_devices()]
    if selector and selector.isdigit():
        idx = int(selector)
        if 0 <= idx < len(devices) and devices[idx].get(key, 0) > 0:
            return idx
        return None
    name = (selector or "Reachy Mini Audio").lower()
    for idx, d in enumerate(devices):
        if name in str(d["name"]).lower() and d.get(key, 0) > 0:
            return idx
    return None


def _ensure_wakeword_assets(download_models: Any) -> dict[str, Path]:
    WAKEWORD_CACHE.mkdir(parents=True, exist_ok=True)
    assets = {
        "embedding": WAKEWORD_CACHE / "embedding_model.onnx",
        "melspectrogram": WAKEWORD_CACHE / "melspectrogram.onnx",
        "wakeword": WAKEWORD_CACHE / f"{WAKEWORD}_v0.1.onnx",
    }
    if not all(p.exists() for p in assets.values()):
        print("Downloading wake word models...")
        download_models([WAKEWORD], target_directory=str(WAKEWORD_CACHE))
    return assets


def _llm_chat(messages: list[dict[str, str]], llm_url: str) -> str:
    payload = json.dumps({
        "messages": messages,
        "max_tokens": 150,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        f"{llm_url}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def _resample(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Simple linear resampling (no external dependency)."""
    if from_sr == to_sr:
        return audio
    ratio = to_sr / from_sr
    new_len = int(len(audio) * ratio)
    indices = np.linspace(0, len(audio) - 1, new_len)
    return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


class Conversation:
    def __init__(self, deps: dict[str, Any], args: argparse.Namespace):
        self.sd = deps["sd"]
        self.sf = deps["sf"]
        self.args = args
        self.llm_url = args.llm_url
        self.running = True
        self.conversation_history: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        self.mini = None

        # Resolve audio devices
        self.input_device = _resolve_device(self.sd, args.input_device, want_input=True)
        self.output_device = _resolve_device(self.sd, args.output_device, want_input=False)
        if self.input_device is None:
            print("ERROR: No input audio device found", file=sys.stderr)
            sys.exit(1)
        dev_info = self.sd.query_devices(self.input_device)
        print(f"Mic: [{self.input_device}] {dev_info['name']}")
        if self.output_device is not None:
            out_info = self.sd.query_devices(self.output_device)
            print(f"Speaker: [{self.output_device}] {out_info['name']}")

        # Load models
        print("Loading wake word model...")
        assets = _ensure_wakeword_assets(deps["download_models"])
        self.wake_model = deps["WakewordModel"](
            wakeword_models=[str(assets["wakeword"])],
            inference_framework="onnx",
            melspec_model_path=str(assets["melspectrogram"]),
            embedding_model_path=str(assets["embedding"]),
        )

        print("Loading STT model (faster-whisper small.en)...")
        self.whisper = deps["WhisperModel"]("small.en", device="cpu", compute_type="int8")

        print("Loading TTS model (KittenTTS)...")
        self.tts = deps["KittenTTS"]("KittenML/kitten-tts-nano-0.8", backend="cpu")

        # Connect to robot
        if not args.no_robot:
            try:
                from reachy_mini import ReachyMini
                self.mini = ReachyMini(media_backend="no_media")
                self.mini.__enter__()
                print(f"Robot connected (media_released={self.mini.media_released})")
            except Exception as exc:
                print(f"WARNING: Could not connect to robot: {exc}")
                print("Running in audio-only mode.")

    def _gesture_async(self, gesture: str) -> None:
        """Fire-and-forget gesture on the robot."""
        if self.mini is None:
            return
        def _do() -> None:
            try:
                if gesture == "listening":
                    self.mini.goto_target(antennas=[0.3, 0.3], duration=0.3)
                elif gesture == "thinking":
                    self.mini.goto_target(antennas=[0.15, -0.15], duration=0.4)
                    time.sleep(0.3)
                    self.mini.goto_target(antennas=[-0.15, 0.15], duration=0.4)
                elif gesture == "speaking":
                    self.mini.goto_target(antennas=[0.0, 0.0], duration=0.3)
                elif gesture == "idle":
                    self.mini.goto_target(antennas=[0.0, 0.0], duration=0.5)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def listen_for_wakeword(self) -> bool:
        """Block until wake word is detected. Returns False if shutting down."""
        print("\nListening for 'Hey Jarvis'...")
        self.wake_model.reset()

        with self.sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self.input_device,
            blocksize=WAKEWORD_CHUNK_SAMPLES,
        ) as stream:
            while self.running:
                audio, _ = stream.read(WAKEWORD_CHUNK_SAMPLES)
                audio_16 = audio.flatten().astype(np.int16)
                self.wake_model.predict(audio_16)
                # openWakeWord may key by model filename without extension
                scores = None
                for key in self.wake_model.prediction_buffer:
                    if WAKEWORD in key:
                        scores = self.wake_model.prediction_buffer[key]
                        break
                if scores and len(scores) > 0 and scores[-1] > 0.5:
                    print("Wake word detected!")
                    return True
        return False

    def record_utterance(self) -> np.ndarray | None:
        """Record until silence is detected. Returns float32 mono audio."""
        print("Listening... (speak now)")
        self._gesture_async("listening")
        chunks: list[np.ndarray] = []
        silence_samples = 0
        max_samples = int(MAX_RECORD_SECONDS * SAMPLE_RATE)
        total_samples = 0
        block_size = int(SAMPLE_RATE * 0.1)  # 100ms blocks

        with self.sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self.input_device,
            blocksize=block_size,
        ) as stream:
            while self.running and total_samples < max_samples:
                audio, _ = stream.read(block_size)
                mono = audio.flatten()
                chunks.append(mono)
                total_samples += len(mono)

                rms = float(np.sqrt(np.mean(np.square(mono))))
                if rms < SILENCE_THRESHOLD:
                    silence_samples += len(mono)
                else:
                    silence_samples = 0

                if silence_samples >= int(SILENCE_DURATION * SAMPLE_RATE) and total_samples > SAMPLE_RATE:
                    break

        if not chunks:
            return None
        audio = np.concatenate(chunks)
        # Trim trailing silence
        trim_samples = min(int(SILENCE_DURATION * SAMPLE_RATE), len(audio) // 2)
        return audio[:-trim_samples] if trim_samples > 0 else audio

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio to text."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            self.sf.write(tmp.name, audio, SAMPLE_RATE)
            tmp.close()
            segments, _ = self.whisper.transcribe(tmp.name, language="en")
            text = " ".join(s.text.strip() for s in segments).strip()
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        return text

    def get_response(self, user_text: str) -> str:
        """Send text to LLM and get response."""
        self._gesture_async("thinking")
        self.conversation_history.append({"role": "user", "content": user_text})

        # Keep history manageable (system + last 10 turns)
        if len(self.conversation_history) > 21:
            self.conversation_history = (
                self.conversation_history[:1] + self.conversation_history[-20:]
            )

        try:
            response = _llm_chat(self.conversation_history, self.llm_url)
        except Exception as exc:
            print(f"LLM error: {exc}", file=sys.stderr)
            response = "Sorry, I had trouble thinking about that. Could you try again?"

        self.conversation_history.append({"role": "assistant", "content": response})
        return response

    def speak(self, text: str) -> None:
        """Synthesize and play speech."""
        self._gesture_async("speaking")
        try:
            audio = np.asarray(
                self.tts.generate(text, voice="expr-voice-5-m", speed=1.25),
                dtype=np.float32,
            )
            if self.output_device is not None:
                # Resample if output device runs at a different rate (Reachy = 16kHz)
                out_info = self.sd.query_devices(self.output_device)
                out_sr = int(out_info["default_samplerate"])
                play_audio = _resample(audio, TTS_SAMPLE_RATE, out_sr)
                self.sd.play(play_audio, out_sr, device=self.output_device)
                self.sd.wait()
            else:
                print("(no speaker — skipping playback)")
        except Exception as exc:
            print(f"TTS/playback error: {exc}", file=sys.stderr)
        self._gesture_async("idle")

    def run(self) -> None:
        """Main conversation loop."""
        print("\n=== Reachy Mini Local Conversation ===")
        print("Say 'Hey Jarvis' to start talking.")
        print("Press Ctrl+C to exit.\n")

        # Greeting
        self.speak("Hey! I'm Jarvis. Say hey Jarvis whenever you want to chat.")

        while self.running:
            if not self.listen_for_wakeword():
                break

            audio = self.record_utterance()
            if audio is None or len(audio) < SAMPLE_RATE * 0.3:
                print("(too short, ignoring)")
                continue

            print("Transcribing...")
            user_text = self.transcribe(audio)
            if not user_text or len(user_text.strip()) < 2:
                print("(empty transcription, ignoring)")
                continue
            print(f"You: {user_text}")

            print("Thinking...")
            response = self.get_response(user_text)
            print(f"Jarvis: {response}")

            self.speak(response)

    def shutdown(self) -> None:
        self.running = False
        if self.mini is not None:
            try:
                self.mini.__exit__(None, None, None)
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reachy Mini local voice conversation")
    parser.add_argument("--input-device", default=None, help="Input device index or name")
    parser.add_argument("--output-device", default=None, help="Output device index or name")
    parser.add_argument("--no-robot", action="store_true", help="Audio only, no robot connection")
    parser.add_argument("--llm-url", default=DEFAULT_LLAMA_URL, help="llama-server URL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Check llama-server is reachable
    try:
        req = urllib.request.Request(f"{args.llm_url}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            health = json.loads(resp.read())
        if health.get("status") != "ok":
            print(f"llama-server not ready: {health}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Cannot reach llama-server at {args.llm_url}: {exc}", file=sys.stderr)
        print("Start it with:", file=sys.stderr)
        print("  llama-server --model ~/.cache/reachy_mini/models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8081", file=sys.stderr)
        return 1

    deps = _import_deps()
    conv = Conversation(deps, args)

    def _sigint(sig: int, frame: Any) -> None:
        print("\nShutting down...")
        conv.shutdown()

    signal.signal(signal.SIGINT, _sigint)

    try:
        conv.run()
    finally:
        conv.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
