from __future__ import annotations

import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from .text_normalizer import clean_for_speech

log = logging.getLogger(__name__)


class TTS:
    MIN_SPEED = 0.7
    MAX_SPEED = 2.0
    DEFAULT_SPEED = 1.35

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._synth_proc: subprocess.Popen | None = None
        self._play_proc: subprocess.Popen | None = None
        self._stop_requested = threading.Event()

    @property
    def speed(self) -> float:
        try:
            value = float(self.config.get("speed", self.DEFAULT_SPEED))
        except (TypeError, ValueError):
            value = self.DEFAULT_SPEED
        return max(self.MIN_SPEED, min(self.MAX_SPEED, value))

    def set_speed(self, speed: float) -> float:
        value = max(self.MIN_SPEED, min(self.MAX_SPEED, float(speed)))
        self.config["speed"] = round(value, 2)
        return self.config["speed"]

    def stop(self) -> None:
        """Немедленно остановить текущий синтез/воспроизведение."""
        self._stop_requested.set()

        with self._lock:
            procs = [self._play_proc, self._synth_proc]

        for proc in procs:
            if proc is None:
                continue
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.7)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                log.exception("Failed to stop TTS process")

    def _clear_processes(self):
        with self._lock:
            self._synth_proc = None
            self._play_proc = None

    def speak(self, text: str) -> None:
        if not self.config.get("enabled", True) or not text.strip():
            return

        speech_text = clean_for_speech(text)
        if not speech_text:
            return

        self._stop_requested.clear()
        speed = self.speed

        model = Path(self.config.get("piper_model_path", "")).expanduser()
        piper = shutil.which("piper")

        if piper and model.exists():
            wav = tempfile.NamedTemporaryFile(
                prefix="stas-jarvis-",
                suffix=".wav",
                delete=False,
            )
            wav.close()

            try:
                # Piper uses phoneme length rather than a human-friendly
                # multiplier: smaller length_scale means faster speech.
                length_scale = round(1.0 / speed, 3)

                synth = subprocess.Popen(
                    [
                        piper,
                        "--model", str(model),
                        "--output_file", wav.name,
                        "--length_scale", str(length_scale),
                        "--sentence_silence", "0.05",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                with self._lock:
                    self._synth_proc = synth

                try:
                    _, stderr = synth.communicate(input=speech_text, timeout=120)
                except subprocess.TimeoutExpired:
                    synth.kill()
                    _, stderr = synth.communicate()
                    log.warning("Piper synthesis timed out")
                    return

                with self._lock:
                    self._synth_proc = None

                if self._stop_requested.is_set():
                    return

                if synth.returncode != 0:
                    log.warning("Piper failed: %s", (stderr or "")[-1000:])
                else:
                    player = shutil.which("aplay") or shutil.which("paplay")
                    if player:
                        play = subprocess.Popen(
                            [player, wav.name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        with self._lock:
                            self._play_proc = play

                        while play.poll() is None:
                            if self._stop_requested.wait(0.05):
                                try:
                                    play.terminate()
                                except Exception:
                                    pass
                                break

                        try:
                            play.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            try:
                                play.kill()
                            except Exception:
                                pass

                        with self._lock:
                            self._play_proc = None
                        return

            except Exception:
                log.exception("Piper TTS failed")
            finally:
                self._clear_processes()
                try:
                    Path(wav.name).unlink(missing_ok=True)
                except Exception:
                    pass

        if self._stop_requested.is_set():
            return

        # Fallback: espeak uses words-per-minute. Keep its perceived speed in
        # roughly the same ballpark as the Piper multiplier.
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if espeak:
            try:
                wpm = int(175 * speed)
                proc = subprocess.Popen(
                    [
                        espeak,
                        "-v",
                        self.config.get("fallback_espeak_voice", "ru"),
                        "-s",
                        str(wpm),
                        speech_text,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                with self._lock:
                    self._play_proc = proc

                while proc.poll() is None:
                    if self._stop_requested.wait(0.05):
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        break

                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            finally:
                self._clear_processes()
