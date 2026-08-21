from __future__ import annotations

import json
import logging
import threading

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from .config import ConfigStore, LOG_PATH
from .logging_setup import tail_log
from .llm import LLMClient
from .tools import ToolRegistry
from .agent import Agent
from .tts import TTS
from .voice import VoiceListener
from .local_intents import try_local_intent

log = logging.getLogger(__name__)

HELP = """[bold]Команды[/bold]
/help                       помощь
/status                     текущий provider/model/voice
/provider <lmstudio|openrouter>
/model <model-id>
/models [фильтр]            модели текущего provider
/voice on|off
/tts on|off
/stop                       остановить текущую озвучку
/logs [N]                   последние N строк лога
/tools                      список tools
/devices                    список аудиоустройств
/mic <id|default>           выбрать микрофон
/clear                      новая сессия
/quit                       выход

Можно просто печатать сообщения. Голосовой listener работает параллельно.
"""

class JarvisApp(App):
    TITLE = "STAS JARVIS"
    SUB_TITLE = "voice + tools + LM Studio/OpenRouter"

    CSS = """
    Screen { layout: vertical; }
    #chat { height: 1fr; border: solid $primary; padding: 0 1; }
    #status { height: 1; padding: 0 1; background: $panel; }
    #input { dock: bottom; }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear UI"),
        ("escape", "stop_speech", "Stop speech"),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = ConfigStore()
        self.llm = LLMClient(self.cfg)
        self.tools = ToolRegistry(self.cfg.data)
        self.agent = Agent(self.llm, self.tools, self.cfg.data)
        self.tts = TTS(self.cfg.data["tts"])
        self.voice: VoiceListener | None = None
        self.agent_lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat", wrap=True, markup=True, highlight=True)
        yield Static("", id="status")
        yield Input(placeholder="Напиши команду или скажи «Джарвис»…", id="input")
        yield Footer()

    def on_mount(self):
        self._refresh_status()
        self.chat.write("[bold green]STAS JARVIS[/bold green] запущен. /help для команд.")
        if self.cfg.data["voice"].get("enabled", True):
            self._start_voice()
        self.query_one("#input", Input).focus()

    @property
    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def _refresh_status(self):
        voice = "ON" if self.cfg.data["voice"].get("enabled", True) else "OFF"
        tts = "ON" if self.cfg.data["tts"].get("enabled", True) else "OFF"
        self.query_one("#status", Static).update(
            f" provider={self.cfg.active_provider_name} | model={self.cfg.provider['model']} | voice={voice} | tts={tts} "
        )

    def _voice_status(self, text: str):
        try:
            self.call_from_thread(self._write_status_event, text)
        except Exception:
            pass

    def _write_status_event(self, text: str):
        self.chat.write(f"[dim]🎤 {escape(text)}[/dim]")

    def _on_voice_command(self, text: str):
        try:
            self.call_from_thread(self._submit_text, text, "voice")
        except Exception:
            log.exception("Cannot submit voice command to UI")

    def _start_voice(self):
        if self.voice:
            self.voice.stop()
        self.voice = VoiceListener(
            self.cfg.data["voice"],
            on_command=self._on_voice_command,
            on_status=self._voice_status,
        )
        self.voice.start()

    def _stop_voice(self):
        if self.voice:
            self.voice.stop()
            self.voice = None

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text == "/quit":
            self.exit()
            return
        self._submit_text(text, "text")

    def _submit_text(self, text: str, source: str):
        if text.startswith("/"):
            self.run_worker(lambda: self._handle_command(text), thread=True)
            return
        prefix = "🎙️" if source == "voice" else "›"
        self.chat.write(f"[bold cyan]{prefix} Ты:[/bold cyan] {escape(text)}")
        self.run_worker(lambda: self._ask_worker(text), thread=True)

    def _ask_worker(self, text: str):
        with self.agent_lock:
            try:
                local_answer = try_local_intent(text)
                if local_answer is not None:
                    self.call_from_thread(
                        self.chat.write,
                        f"[bold green]JARVIS:[/bold green] {escape(local_answer)} [dim](локально)[/dim]"
                    )
                    if self.cfg.data["ui"].get("speak_responses", True) and self.cfg.data["tts"].get("enabled", True):
                        if self.voice:
                            self.voice.set_paused(True)
                        try:
                            self.tts.speak(local_answer)
                        finally:
                            if self.voice:
                                self.voice.set_paused(False)
                    return

                def on_tool(name, args, result):
                    self.call_from_thread(
                        self.chat.write,
                        f"[yellow]⚙ {escape(name)}[/yellow] [dim]{escape(json.dumps(args, ensure_ascii=False)[:500])}[/dim]"
                    )
                answer = self.agent.ask(text, on_tool=on_tool)
                self.call_from_thread(
                    self.chat.write,
                    f"[bold green]JARVIS:[/bold green] {escape(answer)}"
                )

                if self.cfg.data["ui"].get("speak_responses", True) and self.cfg.data["tts"].get("enabled", True):
                    if self.voice:
                        self.voice.set_paused(True)
                    try:
                        self.tts.speak(answer)
                    finally:
                        if self.voice:
                            self.voice.set_paused(False)
            except Exception as e:
                log.exception("Agent request failed")
                self.call_from_thread(
                    self.chat.write,
                    f"[bold red]Ошибка:[/bold red] {escape(str(e))}\n[dim]Смотри /logs[/dim]"
                )

    def _handle_command(self, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        try:
            if cmd == "/help":
                self.call_from_thread(self.chat.write, HELP)

            elif cmd == "/status":
                p = self.cfg.provider
                msg = (
                    f"[bold]provider:[/bold] {self.cfg.active_provider_name}\n"
                    f"[bold]model:[/bold] {p['model']}\n"
                    f"[bold]base_url:[/bold] {p['base_url']}\n"
                    f"[bold]voice:[/bold] {self.cfg.data['voice'].get('enabled', True)}\n"
                    f"[bold]whisper:[/bold] {self.cfg.data['voice'].get('whisper_model')}\n"
                    f"[bold]log:[/bold] {LOG_PATH}"
                )
                self.call_from_thread(self.chat.write, msg)

            elif cmd == "/provider":
                if not arg:
                    self.call_from_thread(self.chat.write, "Providers: " + ", ".join(self.cfg.data["providers"].keys()))
                else:
                    self.cfg.set_provider(arg)
                    self.call_from_thread(self._refresh_status)
                    self.call_from_thread(self.chat.write, f"Provider → [bold]{escape(arg)}[/bold]")

            elif cmd == "/model":
                if not arg:
                    self.call_from_thread(self.chat.write, f"Model: {escape(self.cfg.provider['model'])}")
                else:
                    self.cfg.set_model(arg)
                    self.call_from_thread(self._refresh_status)
                    self.call_from_thread(self.chat.write, f"Model → [bold]{escape(arg)}[/bold]")

            elif cmd == "/models":
                models = self.llm.list_models(arg, limit=40)
                self.call_from_thread(self.chat.write, "\n".join(escape(m) for m in models) or "Ничего не найдено.")

            elif cmd == "/voice":
                value = arg.lower()
                if value not in ("on", "off"):
                    self.call_from_thread(self.chat.write, "Использование: /voice on|off")
                else:
                    enabled = value == "on"
                    self.cfg.data["voice"]["enabled"] = enabled
                    self.cfg.save()
                    if enabled:
                        self.call_from_thread(self._start_voice)
                    else:
                        self.call_from_thread(self._stop_voice)
                    self.call_from_thread(self._refresh_status)

            elif cmd == "/tts":
                value = arg.lower()
                if value not in ("on", "off"):
                    self.call_from_thread(self.chat.write, "Использование: /tts on|off")
                else:
                    enabled = value == "on"
                    self.cfg.data["tts"]["enabled"] = enabled
                    self.cfg.data["ui"]["speak_responses"] = enabled
                    self.cfg.save()
                    self.call_from_thread(self._refresh_status)

            elif cmd == "/stop":
                self.tts.stop()
                self.call_from_thread(
                    self.chat.write,
                    "[dim]🔇 Озвучка остановлена.[/dim]"
                )

            elif cmd == "/logs":
                n = int(arg) if arg.isdigit() else 80
                self.call_from_thread(self.chat.write, "[bold]LOG[/bold]\n" + escape(tail_log(n)))

            elif cmd == "/tools":
                names = [x["function"]["name"] for x in self.tools.schemas()]
                self.call_from_thread(self.chat.write, "\n".join("• " + n for n in names))

            elif cmd == "/devices":
                import sounddevice as sd
                devices = sd.query_devices()
                lines = []
                current = self.cfg.data["voice"].get("input_device")
                for i, d in enumerate(devices):
                    if d.get("max_input_channels", 0) <= 0:
                        continue
                    mark = "  ← current" if current == i else ""
                    lines.append(
                        f"{i}: {d['name']} | inputs={d['max_input_channels']} | "
                        f"default_sr={int(d['default_samplerate'])}{mark}"
                    )
                self.call_from_thread(
                    self.chat.write,
                    "\n".join(escape(x) for x in lines) or "Input-устройств не найдено."
                )

            elif cmd == "/mic":
                value = arg.strip()
                if not value:
                    self.call_from_thread(
                        self.chat.write,
                        f"Текущий input_device: {self.cfg.data['voice'].get('input_device')}"
                    )
                else:
                    if value.lower() == "default":
                        device = None
                    else:
                        device = int(value)
                    self.cfg.data["voice"]["input_device"] = device
                    self.cfg.save()
                    if self.cfg.data["voice"].get("enabled", True):
                        self.call_from_thread(self._start_voice)
                    self.call_from_thread(
                        self.chat.write,
                        f"Микрофон → {escape(str(value))}"
                    )

            elif cmd == "/clear":
                self.agent.clear()
                self.call_from_thread(self.chat.clear)
                self.call_from_thread(self.chat.write, "[dim]Новая сессия.[/dim]")

            else:
                self.call_from_thread(self.chat.write, f"Неизвестная команда: {escape(cmd)}. /help")
        except Exception as e:
            log.exception("Slash command failed")
            self.call_from_thread(self.chat.write, f"[red]{escape(str(e))}[/red]")

    def action_stop_speech(self):
        self.tts.stop()
        self.chat.write("[dim]🔇 Озвучка остановлена.[/dim]")

    def action_clear_chat(self):
        self.chat.clear()

    def on_unmount(self):
        self._stop_voice()
        try:
            self.tools.browser.close()
        except Exception:
            pass
