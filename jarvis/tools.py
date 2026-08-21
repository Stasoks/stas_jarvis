from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from datetime import datetime
from typing import Any

from .browser import BrowserManager

log = logging.getLogger(__name__)

def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)

class ToolRegistry:
    def __init__(self, config: dict):
        self.config = config
        self.browser = BrowserManager(headless=config["browser"].get("headless", False))
        self.allowed_roots = [Path(p).expanduser().resolve() for p in config["tools"].get("allowed_roots", ["~"])]

        self._tools = {
            "get_current_datetime": self.get_current_datetime,
            "open_application": self.open_application,
            "open_folder": self.open_folder,
            "open_url": self.open_url,
            "media_control": self.media_control,
            "set_volume": self.set_volume,
            "set_brightness": self.set_brightness,
            "focus_window": self.focus_window,
            "type_text": self.type_text,
            "press_keys": self.press_keys,
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "search_files": self.search_files,
            "run_shell": self.run_shell,
            "browser_open": self.browser_open,
            "browser_search": self.browser_search,
            "browser_read": self.browser_read,
            "browser_list_elements": self.browser_list_elements,
            "browser_click": self.browser_click,
            "browser_fill": self.browser_fill,
            "browser_back": self.browser_back,
        }

    def schemas(self) -> list[dict[str, Any]]:
        def f(name, desc, props=None, required=None):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props or {},
                        "required": required or [],
                        "additionalProperties": False,
                    },
                },
            }

        return [
            f("get_current_datetime",
              "Получить точные локальные дату, время и часовой пояс компьютера. "
              "Используй для вопросов 'который час', 'сколько времени', 'какая дата'."),
            f("open_application", "Открыть установленное Linux-приложение по человеческому названию.",
              {"name": {"type": "string"}}, ["name"]),
            f("open_folder",
              "Найти папку в домашнем каталоге по человеческому названию и открыть её. "
              "Подходит для 'открой папку проекты/загрузки/документы'.",
              {"name": {"type": "string"}}, ["name"]),
            f("open_url", "Открыть URL в браузере пользователя через xdg-open.",
              {"url": {"type": "string"}}, ["url"]),
            f("media_control", "Управлять текущим медиаплеером через playerctl.",
              {"action": {"type": "string", "enum": ["play", "pause", "play-pause", "next", "previous", "stop", "status", "metadata"]}},
              ["action"]),
            f("set_volume", "Установить системную громкость 0-100 или переключить mute.",
              {"percent": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
               "mute": {"type": ["boolean", "null"]}}),
            f("set_brightness", "Установить яркость экрана в процентах.",
              {"percent": {"type": "integer", "minimum": 1, "maximum": 100}}, ["percent"]),
            f("focus_window", "Переключиться на окно по части заголовка.",
              {"title": {"type": "string"}}, ["title"]),
            f("type_text", "Напечатать текст в активное окно. На Wayland может не работать.",
              {"text": {"type": "string"}}, ["text"]),
            f("press_keys", "Нажать сочетание клавиш через xdotool, например ctrl+l или alt+f4.",
              {"keys": {"type": "string"}}, ["keys"]),
            f("list_files", "Показать файлы и папки.",
              {"path": {"type": "string"}}, ["path"]),
            f("read_file", "Прочитать текстовый файл.",
              {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 100, "maximum": 30000}},
              ["path"]),
            f("write_file", "Создать или перезаписать текстовый файл.",
              {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            f("search_files", "Найти файлы по имени внутри папки.",
              {"path": {"type": "string"}, "query": {"type": "string"}}, ["path", "query"]),
            f("run_shell", "Выполнить shell-команду. Предпочитай специализированные tools. sudo и разрушительные команды блокируются.",
              {"command": {"type": "string"}, "cwd": {"type": ["string", "null"]}}, ["command"]),
            f("browser_open", "Открыть страницу в управляемом Chromium.",
              {"url": {"type": "string"}}, ["url"]),
            f("browser_search", "Найти запрос в интернете через управляемый Chromium.",
              {"query": {"type": "string"}}, ["query"]),
            f("browser_read", "Прочитать текст текущей веб-страницы."),
            f("browser_list_elements", "Получить интерактивные элементы текущей страницы с ref вида e1,e2. Вызывай перед click/fill."),
            f("browser_click", "Кликнуть элемент по ref из browser_list_elements.",
              {"ref": {"type": "string"}}, ["ref"]),
            f("browser_fill", "Заполнить поле по ref; при необходимости нажать Enter.",
              {"ref": {"type": "string"}, "text": {"type": "string"}, "press_enter": {"type": "boolean"}},
              ["ref", "text"]),
            f("browser_back", "Вернуться на предыдущую страницу."),
        ]

    def execute(self, name: str, args: dict[str, Any]) -> str:
        fn = self._tools.get(name)
        if not fn:
            return _result(False, f"Неизвестный tool: {name}")
        log.info("TOOL %s %s", name, json.dumps(args, ensure_ascii=False)[:1000])
        try:
            out = fn(**args)
            log.info("TOOL RESULT %s %s", name, str(out)[:1200])
            return out
        except Exception as e:
            log.exception("Tool failed: %s", name)
            return _result(False, f"{type(e).__name__}: {e}")

    def _safe_path(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        if not any(p == root or root in p.parents for root in self.allowed_roots):
            raise PermissionError(f"Путь вне allowed_roots: {p}")
        return p

    def _run(self, args, timeout=20, shell=False, cwd=None):
        cp = subprocess.run(
            args, shell=shell, cwd=cwd, text=True,
            capture_output=True, timeout=timeout
        )
        return cp.returncode, cp.stdout.strip(), cp.stderr.strip()

    def get_current_datetime(self) -> str:
        now = datetime.now().astimezone()
        return _result(
            True,
            f"Сейчас {now:%H:%M:%S}, {now:%d.%m.%Y}",
            iso=now.isoformat(),
            timezone=str(now.tzinfo),
            weekday=now.strftime("%A"),
        )

    def open_folder(self, name: str) -> str:
        needle = name.casefold().strip()
        aliases = {
            "загрузки": "Downloads",
            "скачивания": "Downloads",
            "документы": "Documents",
            "рабочий стол": "Desktop",
            "проекты": "projects",
            "проект": "projects",
        }
        target = aliases.get(needle, name)

        candidates = []
        home = Path.home()
        frontier = [(home, 0)]
        while frontier:
            base, depth = frontier.pop(0)
            if depth > 3:
                continue
            try:
                for p in base.iterdir():
                    if not p.is_dir():
                        continue
                    candidates.append(p)
                    if depth < 3 and not p.name.startswith("."):
                        frontier.append((p, depth + 1))
            except (PermissionError, OSError):
                continue

        from rapidfuzz.fuzz import ratio
        best = None
        best_score = -1
        for p in candidates:
            score = ratio(target.casefold(), p.name.casefold())
            if target.casefold() in p.name.casefold():
                score += 35
            if score > best_score:
                best_score = score
                best = p

        if best is None or best_score < 50:
            return _result(False, f"Не нашёл папку, похожую на '{name}'")

        subprocess.Popen(
            ["xdg-open", str(best)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _result(True, f"Открываю папку {best}", path=str(best))

    def open_application(self, name: str) -> str:
        needle = name.casefold().strip()
        aliases = {
            "терминал": "terminal", "калькулятор": "calculator",
            "телеграм": "telegram", "файрфокс": "firefox",
            "браузер": "firefox", "настройки": "settings",
        }
        needle = aliases.get(needle, needle)

        desktop_dirs = [Path.home()/".local/share/applications", Path("/usr/share/applications")]
        best = None
        best_score = -1
        from rapidfuzz.fuzz import ratio

        for d in desktop_dirs:
            if not d.exists():
                continue
            for f in d.glob("*.desktop"):
                try:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                names = re.findall(r"^Name(?:\[[^\]]+\])?=(.+)$", txt, flags=re.M)
                for display in names[:5]:
                    score = ratio(needle, display.casefold())
                    if needle in display.casefold():
                        score += 30
                    if score > best_score:
                        best_score = score
                        best = (f, display)

        if best and best_score >= 45:
            desktop_file, display = best
            desktop_id = desktop_file.stem
            if shutil.which("gtk-launch"):
                subprocess.Popen(["gtk-launch", desktop_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["gio", "launch", str(desktop_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return _result(True, f"Открываю {display}")

        exe = shutil.which(name)
        if exe:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return _result(True, f"Запущено: {exe}")
        return _result(False, f"Не нашёл приложение '{name}'")

    def open_url(self, url: str) -> str:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _result(True, f"Открыт URL: {url}")

    def media_control(self, action: str) -> str:
        if not shutil.which("playerctl"):
            return _result(False, "playerctl не установлен")
        if action == "metadata":
            rc, out, err = self._run(["playerctl", "metadata", "--format", "{{artist}} — {{title}}"])
        elif action == "status":
            rc, out, err = self._run(["playerctl", "status"])
        else:
            rc, out, err = self._run(["playerctl", action])
        return _result(rc == 0, out or (f"media: {action}" if rc == 0 else err))

    def set_volume(self, percent: int | None = None, mute: bool | None = None) -> str:
        if shutil.which("wpctl"):
            if mute is not None:
                cmd = ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"]
            elif percent is not None:
                cmd = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%"]
            else:
                return _result(False, "Нужно percent или mute")
        elif shutil.which("pactl"):
            if mute is not None:
                cmd = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"]
            elif percent is not None:
                cmd = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"]
            else:
                return _result(False, "Нужно percent или mute")
        else:
            return _result(False, "Не найден wpctl/pactl")
        rc, out, err = self._run(cmd)
        return _result(rc == 0, out or err or "Громкость изменена")

    def set_brightness(self, percent: int) -> str:
        if not shutil.which("brightnessctl"):
            return _result(False, "brightnessctl не установлен")
        rc, out, err = self._run(["brightnessctl", "set", f"{percent}%"])
        return _result(rc == 0, out or err)

    def focus_window(self, title: str) -> str:
        if not shutil.which("wmctrl"):
            return _result(False, "wmctrl не установлен")
        rc, out, err = self._run(["wmctrl", "-a", title])
        return _result(rc == 0, out or err or f"Фокус: {title}")

    def type_text(self, text: str) -> str:
        if not shutil.which("xdotool"):
            return _result(False, "xdotool не установлен")
        rc, out, err = self._run(["xdotool", "type", "--clearmodifiers", "--delay", "15", text])
        return _result(rc == 0, out or err or "Текст введён")

    def press_keys(self, keys: str) -> str:
        if not shutil.which("xdotool"):
            return _result(False, "xdotool не установлен")
        rc, out, err = self._run(["xdotool", "key", "--clearmodifiers", keys])
        return _result(rc == 0, out or err or f"Нажато: {keys}")

    def list_files(self, path: str) -> str:
        p = self._safe_path(path)
        if not p.is_dir():
            return _result(False, f"Не папка: {p}")
        items = []
        for x in sorted(p.iterdir(), key=lambda q: (not q.is_dir(), q.name.casefold()))[:200]:
            items.append({"name": x.name, "type": "dir" if x.is_dir() else "file"})
        return json.dumps({"ok": True, "path": str(p), "items": items}, ensure_ascii=False)

    def read_file(self, path: str, max_chars: int = 16000) -> str:
        p = self._safe_path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        return _result(True, text[:max_chars], path=str(p), truncated=len(text) > max_chars)

    def write_file(self, path: str, content: str) -> str:
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return _result(True, f"Записано {len(content)} символов", path=str(p))

    def search_files(self, path: str, query: str) -> str:
        p = self._safe_path(path)
        needle = query.casefold()
        results = []
        for x in p.rglob("*"):
            if needle in x.name.casefold():
                results.append(str(x))
                if len(results) >= 100:
                    break
        return json.dumps({"ok": True, "results": results}, ensure_ascii=False)

    def run_shell(self, command: str, cwd: str | None = None) -> str:
        if not self.config["tools"].get("allow_shell", True):
            return _result(False, "run_shell отключён в config.json")

        lowered = command.casefold()
        blocked = [
            r"\bsudo\b", r"\bmkfs\b", r"\bdd\s+if=", r"rm\s+-rf\s+/(?:\s|$)",
            r"\bshutdown\b", r"\bpoweroff\b", r"\breboot\b",
            r":\(\)\s*\{\s*:\|:&\s*\};:"
        ]
        if any(re.search(p, lowered) for p in blocked):
            return _result(False, "Команда заблокирована safety-фильтром")

        run_cwd = str(self._safe_path(cwd)) if cwd else str(Path.home())
        rc, out, err = self._run(
            command,
            shell=True,
            cwd=run_cwd,
            timeout=int(self.config["tools"].get("shell_timeout_sec", 30)),
        )
        text = (out + ("\nSTDERR:\n" + err if err else ""))[:16000]
        return _result(rc == 0, text or f"exit_code={rc}", exit_code=rc)

    def browser_open(self, url: str) -> str:
        return _result(True, self.browser.open(url))

    def browser_search(self, query: str) -> str:
        return _result(True, self.browser.search(query))

    def browser_read(self) -> str:
        return _result(True, self.browser.read())

    def browser_list_elements(self) -> str:
        return _result(True, self.browser.elements())

    def browser_click(self, ref: str) -> str:
        return _result(True, self.browser.click(ref))

    def browser_fill(self, ref: str, text: str, press_enter: bool = False) -> str:
        return _result(True, self.browser.fill(ref, text, press_enter))

    def browser_back(self) -> str:
        return _result(True, self.browser.back())
