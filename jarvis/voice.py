from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
import webrtcvad
from rapidfuzz.fuzz import partial_ratio
from vosk import KaldiRecognizer, Model, SetLogLevel

log = logging.getLogger(__name__)
SetLogLevel(-1)


class VoiceListener:
    """
    Один непрерывный audio stream:

        Vosk wake-word
            ↓
        тот же самый stream
            ↓
        запись команды
            ↓
        Whisper

    Это принципиально: мы НЕ закрываем и НЕ переоткрываем микрофон после
    слова "Джарвис", поэтому начало команды не исчезает в дыре между stream'ами.
    """

    def __init__(self, config: dict, on_command, on_status=None):
        self.config = config
        self.on_command = on_command
        self.on_status = on_status or (lambda _: None)

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread: threading.Thread | None = None

        self._whisper = None
        self._whisper_lock = threading.Lock()
        self._whisper_ready = threading.Event()
        self._whisper_error: Exception | None = None
        self._whisper_loader_thread: threading.Thread | None = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return

        self.stop_event.clear()

        if not self._whisper_loader_thread or not self._whisper_loader_thread.is_alive():
            self._whisper_loader_thread = threading.Thread(
                target=self._preload_whisper,
                name="whisper-preloader",
                daemon=True,
            )
            self._whisper_loader_thread.start()

        self.thread = threading.Thread(
            target=self._loop,
            name="voice-listener",
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def set_paused(self, paused: bool):
        if paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()

    def _status(self, text: str):
        log.info("VOICE %s", text)
        try:
            self.on_status(text)
        except Exception:
            pass

    def _device(self):
        value = self.config.get("input_device")
        if value in (None, "", "default"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    def _match_wake(self, text: str) -> bool:
        normalized = text.casefold().strip()
        if not normalized:
            return False

        threshold = int(self.config.get("wake_fuzzy_threshold", 82))
        for wake in self.config.get("wake_words", ["джарвис"]):
            w = wake.casefold()
            if w in normalized:
                return True
            if partial_ratio(w, normalized) >= threshold:
                return True
        return False

    def _strip_wake_word(self, text: str) -> str:
        out = text.strip()
        low = out.casefold()

        for wake in sorted(
            self.config.get("wake_words", ["джарвис"]),
            key=len,
            reverse=True,
        ):
            w = wake.casefold()
            idx = low.find(w)
            if idx != -1 and idx <= 6:
                out = (out[:idx] + out[idx + len(w):]).lstrip(" ,.!?-—")
                low = out.casefold()
                break

        return out.strip()

    def _beep_async(self):
        def worker():
            try:
                sr = int(self.config.get("sample_rate", 16000))
                t = np.linspace(0, 0.075, int(sr * 0.075), False)
                tone = (0.07 * np.sin(2 * np.pi * 900 * t)).astype(np.float32)
                sd.play(tone, sr)
                sd.wait()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _preload_whisper(self):
        try:
            self._load_whisper()
        except Exception as e:
            self._whisper_error = e
            self._whisper_ready.set()
            log.exception("Whisper preload failed")

    def _load_whisper(self):
        if self._whisper is not None:
            self._whisper_ready.set()
            return self._whisper

        with self._whisper_lock:
            if self._whisper is not None:
                self._whisper_ready.set()
                return self._whisper

            from faster_whisper import WhisperModel

            name = self.config.get("whisper_model", "small")
            device = self.config.get("whisper_device", "cpu")
            compute = self.config.get("whisper_compute_type", "int8")

            self._status(f"Загружаю Whisper {name} ({device}/{compute})...")

            model_dir = str(
                Path(
                    self.config.get(
                        "whisper_model_dir",
                        "~/.local/share/stas-jarvis/models/whisper",
                    )
                ).expanduser()
            )
            Path(model_dir).mkdir(parents=True, exist_ok=True)

            self._whisper = WhisperModel(
                name,
                device=device,
                compute_type=compute,
                download_root=model_dir,
            )

            self._whisper_ready.set()
            self._status(f"Whisper {name} готов")
            return self._whisper

    def _wait_for_whisper(self):
        if self._whisper is None and not self._whisper_ready.is_set():
            self._status("Жду готовности Whisper...")
            self._whisper_ready.wait()

        if self._whisper_error:
            raise self._whisper_error

        if self._whisper is None:
            return self._load_whisper()

        return self._whisper

    def _prepare_audio(self, audio_i16: np.ndarray) -> np.ndarray:
        audio = audio_i16.astype(np.float32) / 32768.0
        if audio.size == 0:
            return audio

        audio -= float(np.mean(audio))

        if self.config.get("normalize_audio", True):
            peak = float(np.percentile(np.abs(audio), 99.7))
            if 0.002 < peak < 0.80:
                gain = min(10.0, 0.82 / peak)
                audio = np.clip(audio * gain, -1.0, 1.0)

        return audio.astype(np.float32)

    def _transcribe(self, audio_i16: np.ndarray, sr: int) -> str:
        model = self._wait_for_whisper()
        audio = self._prepare_audio(audio_i16)

        kwargs = dict(
            language="ru",
            beam_size=5,
            best_of=5,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 400,
            },
            condition_on_previous_text=False,
            initial_prompt=self.config.get("whisper_initial_prompt") or None,
            word_timestamps=False,
        )

        hotwords = self.config.get("whisper_hotwords")
        if hotwords:
            kwargs["hotwords"] = hotwords

        try:
            segments, _ = model.transcribe(audio, **kwargs)
        except TypeError as e:
            if "hotwords" not in str(e):
                raise
            kwargs.pop("hotwords", None)
            segments, _ = model.transcribe(audio, **kwargs)

        text = " ".join(seg.text.strip() for seg in segments).strip()
        return self._strip_wake_word(text)

    def _capture_command_from_same_stream(
        self,
        stream,
        prebuffer: deque[bytes],
        frame_samples: int,
    ) -> np.ndarray | None:
        sr = int(self.config.get("sample_rate", 16000))
        frame_ms = int(round(frame_samples / sr * 1000))

        max_seconds = float(self.config.get("command_max_seconds", 25))
        wait_seconds = float(self.config.get("command_wait_seconds", 7))
        silence_ms = int(self.config.get("post_speech_silence_ms", 1100))
        min_rms = float(self.config.get("min_rms_threshold", 120))
        vad_mode = int(self.config.get("vad_aggressiveness", 1))

        vad = webrtcvad.Vad(max(0, min(3, vad_mode)))
        chunks: list[bytes] = list(prebuffer)

        speech_started = False
        speech_frames = 0
        silence_frames = 0
        needed_silence_frames = max(1, silence_ms // frame_ms)

        started_at = time.monotonic()

        self._beep_async()
        self._status(
            f"Слушаю команду… continuous stream, pre-roll≈"
            f"{len(prebuffer) * frame_ms}мс"
        )

        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - started_at > max_seconds:
                break

            data, _ = stream.read(frame_samples)
            b = bytes(data)
            chunks.append(b)

            arr = np.frombuffer(b, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(arr * arr) + 1e-9))

            try:
                vad_speech = vad.is_speech(b, sr)
            except Exception:
                vad_speech = False

            is_speech = vad_speech and rms >= min_rms

            if is_speech:
                speech_started = True
                speech_frames += 1
                silence_frames = 0
            elif speech_started:
                silence_frames += 1

                if (
                    speech_frames >= max(3, int(0.18 * 1000 / frame_ms))
                    and silence_frames >= needed_silence_frames
                ):
                    break

            if not speech_started and now - started_at > wait_seconds:
                break

        if not chunks:
            return None

        audio = np.frombuffer(b"".join(chunks), dtype=np.int16)
        duration = len(audio) / sr
        if duration < 0.25:
            return None

        self._status(f"Записано {duration:.1f}с аудио")
        return audio

    def _loop(self):
        try:
            model_path = Path(self.config["vosk_model_path"]).expanduser()
            if not model_path.exists():
                self._status(f"Vosk model не найден: {model_path}")
                return

            sr = int(self.config.get("sample_rate", 16000))
            frame_ms = 30
            frame_samples = int(sr * frame_ms / 1000)

            pre_roll_ms = int(self.config.get("pre_roll_ms", 900))
            prebuffer_max = max(1, pre_roll_ms // frame_ms)

            model = Model(str(model_path))

            try:
                dev = sd.query_devices(self._device(), "input")
                self._status(f"Микрофон: {dev['name']}")
            except Exception:
                pass

            self._status("Wake listener готов: скажи «Джарвис»")

            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                try:
                    with sd.RawInputStream(
                        samplerate=sr,
                        blocksize=frame_samples,
                        dtype="int16",
                        channels=1,
                        device=self._device(),
                    ) as stream:
                        rec = KaldiRecognizer(model, sr)
                        prebuffer: deque[bytes] = deque(maxlen=prebuffer_max)

                        while (
                            not self.stop_event.is_set()
                            and not self.pause_event.is_set()
                        ):
                            data, _ = stream.read(frame_samples)
                            b = bytes(data)
                            prebuffer.append(b)

                            if rec.AcceptWaveform(b):
                                try:
                                    text = json.loads(rec.Result()).get("text", "")
                                except Exception:
                                    text = ""
                            else:
                                try:
                                    text = json.loads(rec.PartialResult()).get("partial", "")
                                except Exception:
                                    text = ""

                            if not self._match_wake(text):
                                continue

                            self._status("Wake word услышан")

                            audio = self._capture_command_from_same_stream(
                                stream=stream,
                                prebuffer=prebuffer,
                                frame_samples=frame_samples,
                            )

                            rec = KaldiRecognizer(model, sr)
                            prebuffer.clear()

                            if audio is None:
                                self._status("Команда не услышана")
                                continue

                            try:
                                command = self._transcribe(audio, sr)
                            except Exception:
                                log.exception("Whisper transcription failed")
                                self._status("Ошибка Whisper")
                                continue

                            if command:
                                self._status(f"Распознано: {command}")
                                self.on_command(command)
                            else:
                                self._status("Whisper вернул пустую команду")

                except Exception:
                    log.exception("Audio stream failed")
                    self._status("Ошибка микрофона. Повтор через 2с")
                    time.sleep(2)

        except Exception:
            log.exception("Voice listener crashed")
            self._status("Voice listener аварийно остановлен")
