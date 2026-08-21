from __future__ import annotations

import json
import re

from rich.markup import escape


# Фразы про экран не должны зависеть от того, угадали ли мы ровно одну
# формулировку пользователя. Если запрос явно спрашивает о том, что видно
# сейчас на дисплее, отправляем его напрямую в vision.
_SCREEN_NOUNS = (
    "экран",
    "скриншот",
    "дисплей",
    "монитор",
    "окно передо мной",
    "окно сейчас",
)

_SCREEN_VERBS = (
    "что",
    "посмотри",
    "покажи",
    "опиши",
    "видишь",
    "видно",
    "открыто",
    "находится",
    "ошибка",
    "кнопка",
    "прочитай",
    "прочти",
    "скажи",
)

_DIRECT_PATTERNS = (
    r"\bчто\s+(?:сейчас\s+)?(?:у\s+меня\s+)?на\s+экране\b",
    r"\bчто\s+(?:ты\s+)?видишь\b",
    r"\bчто\s+(?:сейчас\s+)?видно\b",
    r"\bпосмотри\s+(?:мне\s+)?на\s+экран\b",
    r"\bпосмотри[, ]+что\s+(?:у\s+меня\s+)?на\s+экране\b",
    r"\bопиши\s+(?:мой\s+|текущий\s+)?экран\b",
    r"\bсделай\s+скриншот\b",
    r"\bкакая\s+ошибка\s+.*(?:экране|окне)\b",
    r"\bгде\s+(?:на\s+экране\s+)?(?:кнопка|иконка|поле|меню)\b",
)


def _normalize(text: str) -> str:
    s = text.casefold().replace("ё", "е")
    s = re.sub(r"[!?.,:;]+", " ", s)
    return " ".join(s.split())


def _is_screen_question(text: str) -> bool:
    s = _normalize(text)

    if any(re.search(pattern, s) for pattern in _DIRECT_PATTERNS):
        return True

    # Более общий fallback. Требуем и объект экрана, и вопрос/действие,
    # чтобы фраза вроде «яркость экрана 40» не отправлялась в vision.
    has_screen = any(noun in s for noun in _SCREEN_NOUNS)
    has_query = any(verb in s for verb in _SCREEN_VERBS)
    return has_screen and has_query


def _run_screen_vision(app, question: str) -> str:
    raw = app.tools.execute("see_screen", {"question": question})
    try:
        data = json.loads(raw)
    except Exception:
        data = {"ok": False, "message": raw}

    answer = str(data.get("message") or "Не удалось проанализировать экран.")

    app.call_from_thread(
        app.chat.write,
        "[yellow]👁 see_screen[/yellow] [dim](реальный скриншот по запросу)[/dim]",
    )

    suffix = "[dim](зрение)[/dim]" if data.get("ok") else "[red](ошибка зрения)[/red]"
    app.call_from_thread(
        app.chat.write,
        f"[bold green]JARVIS:[/bold green] {escape(answer)} {suffix}",
    )
    app._speak(answer)
    return answer


def install_screen_fast_action(app_cls) -> None:
    """Intercept direct screen-vision questions before the general agent loop."""
    if getattr(app_cls, "_stas_screen_fast_action_installed", False):
        return

    original = app_cls._ask_worker

    def patched(self, text: str):
        if not _is_screen_question(text):
            return original(self, text)

        # ВАЖНО: основной LLM здесь вообще не вызывается. Никакая модель не
        # может решить «я не умею делать скриншоты», потому что screenshot +
        # vision выполняются детерминированно приложением.
        with self.agent_lock:
            _run_screen_vision(self, text)
            return

    app_cls._ask_worker = patched
    app_cls._stas_screen_fast_action_installed = True
