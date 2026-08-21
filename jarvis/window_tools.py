from __future__ import annotations

import json
import re
import shutil

from .tools import ToolRegistry


def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)


ALIASES = {
    "browser": ["firefox", "google-chrome", "google chrome", "chromium", "brave", "opera", "yandex", "edge"],
    "браузер": ["firefox", "google-chrome", "google chrome", "chromium", "brave", "opera", "yandex", "edge"],
    "firefox": ["firefox"],
    "файрфокс": ["firefox"],
    "chrome": ["google-chrome", "google chrome", "chrome"],
    "хром": ["google-chrome", "google chrome", "chrome"],
    "visual studio code": ["code", "visual studio code", "vscode"],
    "vs code": ["code", "visual studio code", "vscode"],
    "vscode": ["code", "visual studio code", "vscode"],
    "telegram": ["telegram", "telegramdesktop"],
    "телеграм": ["telegram", "telegramdesktop"],
    "discord": ["discord"],
    "дискорд": ["discord"],
    "spotify": ["spotify"],
    "спотифай": ["spotify"],
    "steam": ["steam"],
    "стим": ["steam"],
    "terminal": ["gnome-terminal", "kgx", "terminal"],
    "терминал": ["gnome-terminal", "kgx", "terminal"],
}


def _patterns(name: str) -> list[str]:
    key = re.sub(r"\s+", " ", name.casefold().strip())
    return ALIASES.get(key, [key])


def _focus_application(self: ToolRegistry, name: str) -> str:
    patterns = _patterns(name)

    # wmctrl gives both WM_CLASS and title in one cheap call. It is much more
    # reliable than asking the LLM to invent xdotool/grep pipelines.
    if shutil.which("wmctrl"):
        rc, out, err = self._run(["wmctrl", "-lx"])
        if rc == 0 and out:
            best = None
            best_score = -1

            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 4:
                    continue
                wid = parts[0]
                wm_class = parts[3].casefold()
                title = parts[4].casefold() if len(parts) >= 5 else ""

                for p in patterns:
                    p = p.casefold()
                    score = 0
                    if p in wm_class:
                        score += 100
                    if p in title:
                        score += 50
                    if wm_class == p:
                        score += 40
                    if score > best_score:
                        best_score = score
                        best = (wid, wm_class, title)

            if best is not None and best_score > 0:
                wid, wm_class, title = best
                rc2, out2, err2 = self._run(["wmctrl", "-ia", wid])
                if rc2 == 0:
                    return _result(
                        True,
                        f"Переключаюсь на {name}",
                        window_id=wid,
                        wm_class=wm_class,
                        title=title,
                    )

                # Some XWayland windows respond better to xdotool activation.
                if shutil.which("xdotool"):
                    try:
                        decimal_id = str(int(wid, 16))
                    except ValueError:
                        decimal_id = wid
                    rc3, out3, err3 = self._run([
                        "xdotool", "windowactivate", "--sync", decimal_id
                    ])
                    if rc3 == 0:
                        return _result(
                            True,
                            f"Переключаюсь на {name}",
                            window_id=wid,
                            wm_class=wm_class,
                            title=title,
                        )

    # Fallback if wmctrl is unavailable or does not see the window.
    if shutil.which("xdotool"):
        for p in patterns:
            for mode in ("--class", "--name"):
                rc, out, err = self._run([
                    "xdotool", "search", "--onlyvisible", mode, p
                ])
                if rc != 0 or not out.strip():
                    continue
                wid = out.splitlines()[0].strip()
                rc2, out2, err2 = self._run([
                    "xdotool", "windowactivate", "--sync", wid
                ])
                if rc2 == 0:
                    return _result(True, f"Переключаюсь на {name}", window_id=wid)

    return _result(
        False,
        f"Не нашёл видимое окно для '{name}'. На чистом Wayland wmctrl/xdotool могут не видеть native Wayland-окна."
    )


def install_window_tools() -> None:
    if getattr(ToolRegistry, "_stas_window_tools_installed", False):
        return

    original_init = ToolRegistry.__init__
    original_schemas = ToolRegistry.schemas

    def patched_init(self, config):
        original_init(self, config)
        self._tools["focus_application"] = self.focus_application

    def patched_schemas(self):
        schemas = original_schemas(self)
        schemas.append({
            "type": "function",
            "function": {
                "name": "focus_application",
                "description": (
                    "Переключить фокус на уже открытое приложение по человеческому имени. "
                    "Используй вместо shell-диагностики для команд вроде 'переключись на браузер', "
                    "'покажи VS Code', 'вернись в Telegram'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        })
        return schemas

    ToolRegistry.focus_application = _focus_application
    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry._stas_window_tools_installed = True
