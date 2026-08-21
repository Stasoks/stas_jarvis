from __future__ import annotations

import json
import re
from typing import Any

from rich.markup import escape


APP_ALIASES = {
    "visual studio code": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "вс код": "Visual Studio Code",
    "код": "Visual Studio Code",
    "телеграм": "Telegram",
    "telegram": "Telegram",
    "файрфокс": "Firefox",
    "firefox": "Firefox",
    "хром": "Google Chrome",
    "гугл хром": "Google Chrome",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "chromium": "Chromium",
    "хромиум": "Chromium",
    "хромиум браузер": "Chromium",
    "браузер хромиум": "Chromium",
    "веб браузер chromium": "Chromium",
    "chromium browser": "Chromium",
    "браузер": "browser",
    "веб браузер": "browser",
    "терминал": "терминал",
    "калькулятор": "калькулятор",
    "файлы": "файлы",
    "проводник": "файлы",
    "steam": "Steam",
    "стим": "Steam",
    "discord": "Discord",
    "дискорд": "Discord",
    "spotify": "Spotify",
    "спотифай": "Spotify",
    "lm studio": "LM Studio",
    "настройки": "настройки",
}


def _parse_tool_result(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"ok": False, "message": raw}
    except Exception:
        return {"ok": False, "message": raw}


def _clean(text: str) -> str:
    text = text.casefold().replace("ё", "е").strip()
    text = text.replace("—", " ").replace("–", " ").replace("-", " ")
    text = re.sub(r"[.!?]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _known_app_from_text(text: str) -> str | None:
    s = _clean(text)
    m = re.match(r"^(?:пожалуйста\s+)?(?:открой|запусти)\s+(.+)$", s)
    if not m:
        return None
    target = m.group(1).strip()
    if target.startswith(("папку ", "папка ", "сайт ", "страницу ", "ссылку ")):
        return None
    return APP_ALIASES.get(target)


def _focus_app_from_text(text: str) -> str | None:
    s = _clean(text)
    focus_verbs = (
        "переключи",
        "переключись",
        "перейди",
        "вернись",
        "покажи",
        "сфокусируй",
        "сфокусируйся",
    )
    if not any(v in s for v in focus_verbs):
        return None

    # Более длинные aliases проверяем первыми.
    for alias in sorted(APP_ALIASES, key=len, reverse=True):
        if alias in s:
            return APP_ALIASES[alias]

    if "браузер" in s or "окно браузера" in s:
        return "browser"
    return None


def _fast_tool_call(app, tool_name: str, args: dict[str, Any], success_text: str | None = None) -> bool:
    raw = app.tools.execute(tool_name, args)
    data = _parse_tool_result(raw)
    message = str(data.get("message") or "Готово.")

    app.call_from_thread(
        app.chat.write,
        f"[yellow]⚡ {escape(tool_name)}[/yellow] [dim]{escape(json.dumps(args, ensure_ascii=False))}[/dim]",
    )

    answer = success_text or message if data.get("ok") else f"Не получилось: {message}"
    app.call_from_thread(
        app.chat.write,
        f"[bold green]JARVIS:[/bold green] {escape(answer)} [dim](локально, 0 токенов)[/dim]",
    )
    app._speak(answer)
    return True


def try_fast_action(app, text: str) -> bool:
    s = _clean(text)

    app_name = _known_app_from_text(text)
    if app_name:
        return _fast_tool_call(
            app,
            "open_application",
            {"name": app_name},
            f"Открываю {app_name}.",
        )

    focus_name = _focus_app_from_text(text)
    if focus_name:
        spoken = "браузер" if focus_name == "browser" else focus_name
        return _fast_tool_call(
            app,
            "focus_application",
            {"name": focus_name},
            f"Переключаюсь на {spoken}.",
        )

    if "яркост" in s:
        m = re.search(r"\b(\d{1,3})\s*%?", s)
        if m:
            value = max(1, min(100, int(m.group(1))))
            return _fast_tool_call(
                app, "set_brightness", {"percent": value}, f"Яркость {value} процентов."
            )

    if "громкост" in s or "звук" in s:
        m = re.search(r"\b(\d{1,3})\s*%?", s)
        if m:
            value = max(0, min(100, int(m.group(1))))
            return _fast_tool_call(
                app, "set_volume", {"percent": value}, f"Громкость {value} процентов."
            )

    if any(x in s for x in ("следующий трек", "следующую песню", "переключи трек")):
        return _fast_tool_call(app, "media_control", {"action": "next"}, "Следующий трек.")
    if any(x in s for x in ("предыдущий трек", "предыдущую песню")):
        return _fast_tool_call(app, "media_control", {"action": "previous"}, "Предыдущий трек.")
    if any(x in s for x in ("поставь на паузу", "музыку на паузу", "пауза музыки")):
        return _fast_tool_call(app, "media_control", {"action": "pause"}, "Пауза.")
    if any(x in s for x in ("продолжи музыку", "возобнови музыку", "включи воспроизведение")):
        return _fast_tool_call(app, "media_control", {"action": "play"}, "Продолжаю.")

    toggles = [
        (("выключи wi fi", "выключи wifi", "выключи вайфай"), "wifi", "off", "Wi-Fi выключен."),
        (("включи wi fi", "включи wifi", "включи вайфай"), "wifi", "on", "Wi-Fi включён."),
        (("выключи bluetooth", "выключи блютуз"), "bluetooth", "off", "Bluetooth выключен."),
        (("включи bluetooth", "включи блютуз"), "bluetooth", "on", "Bluetooth включён."),
        (("включи ночной свет",), "night_light", "on", "Ночной свет включён."),
        (("выключи ночной свет",), "night_light", "off", "Ночной свет выключен."),
        (("включи темную тему",), "dark_mode", "on", "Тёмная тема включена."),
        (("выключи темную тему",), "dark_mode", "off", "Тёмная тема выключена."),
    ]
    for phrases, action, value, reply in toggles:
        if any(p in s for p in phrases):
            return _fast_tool_call(
                app,
                "system_control",
                {"action": action, "value": value},
                reply,
            )

    return False


def install_fast_actions(app_cls) -> None:
    if getattr(app_cls, "_stas_fast_actions_installed", False):
        return

    original = app_cls._ask_worker

    def patched(self, text: str):
        with self.agent_lock:
            try:
                if try_fast_action(self, text):
                    return
            except Exception as exc:
                self.call_from_thread(
                    self.chat.write,
                    f"[red]Ошибка локального действия: {escape(str(exc))}[/red]",
                )
                return
        return original(self, text)

    app_cls._ask_worker = patched
    app_cls._stas_fast_actions_installed = True
