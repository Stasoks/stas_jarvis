from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from PIL import Image

from .tools import ToolRegistry

log = logging.getLogger(__name__)


def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    return {"raw": text}


def _backend() -> str | None:
    session = os.getenv("XDG_SESSION_TYPE", "").casefold()
    if session == "wayland" and shutil.which("ydotool"):
        return "ydotool"
    if shutil.which("xdotool"):
        return "xdotool"
    if shutil.which("ydotool"):
        return "ydotool"
    return None


def _run(args: list[str], timeout: int = 15):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)


def _screen_size(self: ToolRegistry) -> tuple[int, int]:
    # Reuse exactly the screenshot source used by see_screen so coordinates
    # correspond to what the vision model actually sees.
    raw = self._screen_vision._capture_png()
    with Image.open(raw) as im:
        return im.size


def _computer_observe(self: ToolRegistry, goal: str = "") -> str:
    prompt = f"""
Ты модуль computer-use. Посмотри на текущий экран и помоги desktop-агенту выполнить цель:
{goal or 'Определи текущее состояние интерфейса.'}

Верни ТОЛЬКО JSON без markdown:
{{
  "summary": "что сейчас открыто и что важно",
  "elements": [
    {{"label":"видимый элемент", "type":"button|input|link|menu|window|other", "x":0, "y":0, "confidence":0.0}}
  ],
  "suggested_action": {{"action":"click|type|key|scroll|wait|done|ask_user", "target":"...", "x":0, "y":0, "text":"", "keys":""}},
  "done": false,
  "needs_user": false,
  "reason": ""
}}

Координаты x/y указывай НОРМАЛИЗОВАННЫМИ от 0 до 1000 относительно всего изображения:
левый верх (0,0), правый низ (1000,1000). Указывай координаты только для реально видимых элементов.
Если нужен пароль, 2FA, платёж, подтверждение покупки или другое чувствительное действие, needs_user=true.
Не выдумывай скрытые элементы.
""".strip()
    try:
        answer, meta = self._screen_vision.analyze(prompt)
        parsed = _extract_json(answer)
        return _result(True, "Экран проанализирован", observation=parsed, vision=meta)
    except Exception as exc:
        log.exception("computer_observe failed")
        return _result(False, f"Не удалось проанализировать экран: {exc}")


def _to_pixels(self: ToolRegistry, x: int, y: int, normalized: bool = True) -> tuple[int, int, int, int]:
    width, height = _screen_size(self)
    if normalized:
        px = round(max(0, min(1000, int(x))) / 1000 * max(1, width - 1))
        py = round(max(0, min(1000, int(y))) / 1000 * max(1, height - 1))
    else:
        px = max(0, min(width - 1, int(x)))
        py = max(0, min(height - 1, int(y)))
    return px, py, width, height


def _computer_click(
    self: ToolRegistry,
    x: int,
    y: int,
    button: str = "left",
    double: bool = False,
    normalized: bool = True,
) -> str:
    backend = _backend()
    if not backend:
        return _result(False, "Не найден xdotool/ydotool для управления мышью")

    px, py, width, height = _to_pixels(self, x, y, normalized)
    button = button.casefold()
    if backend == "xdotool":
        btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        args = ["xdotool", "mousemove", str(px), str(py), "click"]
        if double:
            args += ["--repeat", "2", "--delay", "100"]
        args += [btn]
        cp = _run(args)
    else:
        code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
        move = _run(["ydotool", "mousemove", "--absolute", str(px), str(py)])
        if move.returncode != 0:
            return _result(False, f"ydotool mousemove: {move.stderr.strip()}")
        click_args = ["ydotool", "click"]
        if double:
            click_args += ["--repeat", "2", "--next-delay", "100"]
        click_args += [code]
        cp = _run(click_args)

    if cp.returncode != 0:
        return _result(False, cp.stderr.strip() or "click failed", backend=backend)
    time.sleep(0.25)
    return _result(True, f"Клик {button} в ({px},{py})", backend=backend, x=px, y=py, screen=[width, height])


def _computer_type(self: ToolRegistry, text: str, press_enter: bool = False) -> str:
    backend = _backend()
    if not backend:
        return _result(False, "Не найден xdotool/ydotool для ввода текста")

    if backend == "xdotool":
        cp = _run(["xdotool", "type", "--clearmodifiers", "--delay", "12", text])
    else:
        cp = _run(["ydotool", "type", "--key-delay", "12", text])
        if cp.returncode != 0:
            cp = _run(["ydotool", "type", text])

    if cp.returncode != 0:
        return _result(False, cp.stderr.strip() or "type failed", backend=backend)
    if press_enter:
        return _computer_key(self, "enter")
    return _result(True, f"Введено {len(text)} символов", backend=backend)


_YDOT_KEYS = {
    "esc": 1, "escape": 1, "tab": 15, "enter": 28, "return": 28,
    "ctrl": 29, "control": 29, "shift": 42, "alt": 56, "space": 57,
    "backspace": 14, "delete": 111,
    "up": 103, "left": 105, "right": 106, "down": 108,
    "a": 30, "c": 46, "v": 47, "x": 45, "z": 44, "l": 38,
    "r": 19, "t": 20, "w": 17, "s": 31, "p": 25, "q": 16,
    "f4": 62,
}


def _computer_key(self: ToolRegistry, keys: str) -> str:
    backend = _backend()
    if not backend:
        return _result(False, "Не найден xdotool/ydotool для клавиатуры")

    keys = keys.strip()
    if backend == "xdotool":
        cp = _run(["xdotool", "key", "--clearmodifiers", keys])
    else:
        parts = [p.strip().casefold() for p in re.split(r"[+]", keys) if p.strip()]
        codes = []
        for p in parts:
            if p not in _YDOT_KEYS:
                return _result(False, f"ydotool: пока не знаю keycode для '{p}'")
            codes.append(_YDOT_KEYS[p])
        events = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
        cp = _run(["ydotool", "key", *events])

    if cp.returncode != 0:
        return _result(False, cp.stderr.strip() or "key failed", backend=backend)
    time.sleep(0.15)
    return _result(True, f"Нажато: {keys}", backend=backend)


def _computer_scroll(self: ToolRegistry, amount: int = 5) -> str:
    backend = _backend()
    if not backend:
        return _result(False, "Не найден xdotool/ydotool для прокрутки")
    amount = max(-20, min(20, int(amount)))
    if amount == 0:
        return _result(True, "Прокрутка 0")

    if backend == "xdotool":
        button = "5" if amount > 0 else "4"
        cp = _run(["xdotool", "click", "--repeat", str(abs(amount)), "--delay", "45", button])
    else:
        # REL_WHEEL: negative generally scrolls down, positive up.
        wheel = -abs(amount) if amount > 0 else abs(amount)
        cp = _run(["ydotool", "mousemove", "-w", "--", "0", str(wheel)])

    if cp.returncode != 0:
        return _result(False, cp.stderr.strip() or "scroll failed", backend=backend)
    time.sleep(0.2)
    return _result(True, f"Прокрутка: {amount}", backend=backend)


def _computer_wait(self: ToolRegistry, seconds: float = 1.0) -> str:
    seconds = max(0.1, min(10.0, float(seconds)))
    time.sleep(seconds)
    return _result(True, f"Подождал {seconds:.1f}с")


def install_computer_tools() -> None:
    if getattr(ToolRegistry, "_stas_computer_tools_installed", False):
        return

    original_init = ToolRegistry.__init__
    original_schemas = ToolRegistry.schemas

    def patched_init(self, config):
        original_init(self, config)
        self._tools.update({
            "computer_observe": self.computer_observe,
            "computer_click": self.computer_click,
            "computer_type": self.computer_type,
            "computer_key": self.computer_key,
            "computer_scroll": self.computer_scroll,
            "computer_wait": self.computer_wait,
        })

    def patched_schemas(self):
        schemas = original_schemas(self)
        schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "computer_observe",
                    "description": (
                        "Посмотреть текущий рабочий стол через vision и получить видимые элементы с координатами 0..1000. "
                        "Используй для native GUI, системных окон или когда DOM-инструменты браузера недостаточны."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"goal": {"type": "string"}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "computer_click",
                    "description": "Кликнуть по координатам из computer_observe. По умолчанию x/y нормализованы 0..1000.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer", "minimum": 0, "maximum": 1000},
                            "y": {"type": "integer", "minimum": 0, "maximum": 1000},
                            "button": {"type": "string", "enum": ["left", "right", "middle"]},
                            "double": {"type": "boolean"},
                            "normalized": {"type": "boolean"},
                        },
                        "required": ["x", "y"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "computer_type",
                    "description": "Напечатать текст в текущий активный GUI-элемент.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "press_enter": {"type": "boolean"},
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "computer_key",
                    "description": "Нажать клавишу или сочетание в desktop GUI, например enter, esc, ctrl+l, alt+f4.",
                    "parameters": {
                        "type": "object",
                        "properties": {"keys": {"type": "string"}},
                        "required": ["keys"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "computer_scroll",
                    "description": "Прокрутить desktop GUI. Положительное amount вниз, отрицательное вверх.",
                    "parameters": {
                        "type": "object",
                        "properties": {"amount": {"type": "integer", "minimum": -20, "maximum": 20}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "computer_wait",
                    "description": "Подождать изменение интерфейса после клика/загрузки.",
                    "parameters": {
                        "type": "object",
                        "properties": {"seconds": {"type": "number", "minimum": 0.1, "maximum": 10}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
        ])
        return schemas

    ToolRegistry.computer_observe = _computer_observe
    ToolRegistry.computer_click = _computer_click
    ToolRegistry.computer_type = _computer_type
    ToolRegistry.computer_key = _computer_key
    ToolRegistry.computer_scroll = _computer_scroll
    ToolRegistry.computer_wait = _computer_wait
    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry._stas_computer_tools_installed = True
