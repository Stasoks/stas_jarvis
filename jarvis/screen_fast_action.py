from __future__ import annotations

import json

from rich.markup import escape


def _is_screen_question(text: str) -> bool:
    s = " ".join(text.casefold().strip().split())
    phrases = (
        "что на экране",
        "что сейчас на экране",
        "посмотри на экран",
        "посмотри что на экране",
        "опиши экран",
        "что я вижу на экране",
        "что у меня открыто",
        "какое окно открыто",
        "что здесь на экране",
    )
    return any(p in s for p in phrases)


def install_screen_fast_action(app_cls) -> None:
    """Intercept direct screen-vision questions before the general agent loop."""
    if getattr(app_cls, "_stas_screen_fast_action_installed", False):
        return

    original = app_cls._ask_worker

    def patched(self, text: str):
        if not _is_screen_question(text):
            return original(self, text)

        with self.agent_lock:
            raw = self.tools.execute("see_screen", {"question": text})
            try:
                data = json.loads(raw)
            except Exception:
                data = {"ok": False, "message": raw}

            answer = str(data.get("message") or "Не удалось проанализировать экран.")
            self.call_from_thread(
                self.chat.write,
                "[yellow]👁 see_screen[/yellow] [dim](скриншот по запросу)[/dim]",
            )
            if data.get("ok"):
                suffix = "[dim](зрение)[/dim]"
            else:
                suffix = "[red](ошибка зрения)[/red]"

            self.call_from_thread(
                self.chat.write,
                f"[bold green]JARVIS:[/bold green] {escape(answer)} {suffix}",
            )
            self._speak(answer)
            return

    app_cls._ask_worker = patched
    app_cls._stas_screen_fast_action_installed = True
