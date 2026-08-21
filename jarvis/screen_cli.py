from __future__ import annotations

import json

from rich.markup import escape


def install_screen_cli(app_cls) -> None:
    """Add /screen [question] command without touching the main agent."""
    if getattr(app_cls, "_stas_screen_cli_installed", False):
        return

    original = app_cls._handle_command

    def patched(self, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd != "/screen":
            return original(self, text)

        question = arg or "Что сейчас видно на экране? Кратко опиши основные окна и важные элементы интерфейса."

        raw = self.tools.execute("see_screen", {"question": question})
        try:
            data = json.loads(raw)
        except Exception:
            data = {"ok": False, "message": raw}

        answer = str(data.get("message") or "Не удалось проанализировать экран.")
        self.call_from_thread(
            self.chat.write,
            "[yellow]👁 /screen[/yellow] [dim](прямой vision test, без основного LLM)[/dim]",
        )
        if data.get("ok"):
            self.call_from_thread(
                self.chat.write,
                f"[bold green]JARVIS:[/bold green] {escape(answer)} [dim](зрение)[/dim]",
            )
            self._speak(answer)
        else:
            self.call_from_thread(
                self.chat.write,
                f"[bold red]Vision error:[/bold red] {escape(answer)}",
            )
        return

    app_cls._handle_command = patched
    app_cls._stas_screen_cli_installed = True
