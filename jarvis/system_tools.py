from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from .tools import ToolRegistry


def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    v = str(value).strip().casefold()
    if v in {"1", "true", "on", "yes", "да", "вкл", "включить", "включено"}:
        return True
    if v in {"0", "false", "off", "no", "нет", "выкл", "выключить", "выключено"}:
        return False
    raise ValueError(f"Не понял булево значение: {value!r}")


def _spawn(argv: list[str]) -> bool:
    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def _command_exists(candidate: str) -> str | None:
    """Return an executable path for PATH names or absolute candidates."""
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p) if p.exists() and p.is_file() else None
    return shutil.which(candidate)


def _robust_open_application(self: ToolRegistry, name: str) -> str:
    """Launch desktop apps without making the LLM debug Linux packaging.

    The old launcher only scanned /usr/share/applications and therefore missed
    common Snap/Flatpak installs.  A request such as "Visual Studio Code" then
    caused several LLM + shell rounds just to discover /snap/bin/code.
    """
    raw = name.strip()
    needle = raw.casefold().strip()

    # Human names -> executable candidates. Ordered from most likely to fallback.
    aliases: dict[str, list[str]] = {
        "visual studio code": ["code", "/snap/bin/code", "codium", "vscodium"],
        "vs code": ["code", "/snap/bin/code", "codium", "vscodium"],
        "vscode": ["code", "/snap/bin/code", "codium", "vscodium"],
        "code": ["code", "/snap/bin/code", "codium", "vscodium"],
        "телеграм": ["telegram-desktop", "/snap/bin/telegram-desktop", "telegram"],
        "telegram": ["telegram-desktop", "/snap/bin/telegram-desktop", "telegram"],
        "файрфокс": ["firefox", "/snap/bin/firefox"],
        "firefox": ["firefox", "/snap/bin/firefox"],
        "chrome": ["google-chrome", "google-chrome-stable", "chromium", "/snap/bin/chromium"],
        "google chrome": ["google-chrome", "google-chrome-stable", "chromium", "/snap/bin/chromium"],
        "хром": ["google-chrome", "google-chrome-stable", "chromium", "/snap/bin/chromium"],
        "chromium": ["chromium", "/snap/bin/chromium"],
        "терминал": ["gnome-terminal", "kgx", "xterm"],
        "terminal": ["gnome-terminal", "kgx", "xterm"],
        "калькулятор": ["gnome-calculator", "kcalc"],
        "calculator": ["gnome-calculator", "kcalc"],
        "файлы": ["nautilus", "thunar", "dolphin"],
        "проводник": ["nautilus", "thunar", "dolphin"],
        "steam": ["steam", "/snap/bin/steam"],
        "стим": ["steam", "/snap/bin/steam"],
        "discord": ["discord", "/snap/bin/discord"],
        "дискорд": ["discord", "/snap/bin/discord"],
        "spotify": ["spotify", "/snap/bin/spotify"],
        "спотифай": ["spotify", "/snap/bin/spotify"],
        "lm studio": ["lm-studio", "lmstudio", "/opt/LM Studio/lm-studio"],
        "настройки": ["gnome-control-center"],
        "settings": ["gnome-control-center"],
    }

    for candidate in aliases.get(needle, []):
        exe = _command_exists(candidate)
        if exe and _spawn([exe]):
            return _result(True, f"Открываю {raw}", executable=exe)

    # If the user already supplied an executable-ish name, try it directly.
    for candidate in (raw, raw.lower().replace(" ", "-"), raw.lower().replace(" ", "")):
        exe = _command_exists(candidate)
        if exe and _spawn([exe]):
            return _result(True, f"Открываю {raw}", executable=exe)

    # Desktop entries from native packages, Snap and Flatpak.
    desktop_dirs = [
        Path.home() / ".local/share/applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
    ]

    from rapidfuzz.fuzz import ratio

    search_terms = [needle]
    if needle in aliases:
        search_terms += [Path(x).name.casefold() for x in aliases[needle]]

    best: tuple[Path, str, str] | None = None
    best_score = -1

    for directory in desktop_dirs:
        if not directory.exists():
            continue
        for desktop_file in directory.glob("*.desktop"):
            try:
                text = desktop_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            names = re.findall(r"^Name(?:\[[^\]]+\])?=(.+)$", text, flags=re.M)
            exec_match = re.search(r"^Exec=(.+)$", text, flags=re.M)
            exec_line = exec_match.group(1).strip() if exec_match else ""

            labels = names[:8] + [desktop_file.stem]
            for label in labels:
                lc = label.casefold()
                score = max(ratio(term, lc) for term in search_terms)
                if any(term and term in lc for term in search_terms):
                    score += 35
                if any(lc and lc in term for term in search_terms):
                    score += 15
                if score > best_score:
                    best_score = score
                    best = (desktop_file, names[0] if names else desktop_file.stem, exec_line)

    if best and best_score >= 55:
        desktop_file, display, exec_line = best

        # gio launch is packaging-agnostic and works with most desktop files.
        if shutil.which("gio") and _spawn(["gio", "launch", str(desktop_file)]):
            return _result(True, f"Открываю {display}", desktop_file=str(desktop_file))

        # Last fallback: execute Exec= after removing desktop placeholders.
        if exec_line:
            try:
                argv = [x for x in shlex.split(exec_line) if not re.fullmatch(r"%[fFuUdDnNickvm]", x)]
                argv = [x.replace("%%", "%") for x in argv]
                if argv and _spawn(argv):
                    return _result(True, f"Открываю {display}", executable=argv[0])
            except Exception:
                pass

    return _result(
        False,
        f"Не нашёл приложение '{raw}' ни в PATH, ни в Snap/Flatpak/desktop entries",
    )


def _system_control(self: ToolRegistry, action: str, value: str | None = None) -> str:
    action = action.strip().casefold()

    if action == "wifi":
        if not shutil.which("nmcli"):
            return _result(False, "nmcli не установлен")
        enabled = _as_bool(value)
        rc, out, err = self._run(["nmcli", "radio", "wifi", "on" if enabled else "off"])
        return _result(rc == 0, out or err or f"Wi-Fi {'включён' if enabled else 'выключен'}")

    if action == "bluetooth":
        if not shutil.which("bluetoothctl"):
            return _result(False, "bluetoothctl не установлен")
        enabled = _as_bool(value)
        rc, out, err = self._run(["bluetoothctl", "power", "on" if enabled else "off"])
        return _result(rc == 0, out or err or f"Bluetooth {'включён' if enabled else 'выключен'}")

    if action == "night_light":
        if not shutil.which("gsettings"):
            return _result(False, "gsettings не найден")
        enabled = _as_bool(value)
        rc, out, err = self._run([
            "gsettings", "set", "org.gnome.settings-daemon.plugins.color",
            "night-light-enabled", "true" if enabled else "false",
        ])
        return _result(rc == 0, out or err or f"Ночной свет {'включён' if enabled else 'выключен'}")

    if action == "dark_mode":
        if not shutil.which("gsettings"):
            return _result(False, "gsettings не найден")
        enabled = _as_bool(value)
        rc, out, err = self._run([
            "gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
            "prefer-dark" if enabled else "default",
        ])
        return _result(rc == 0, out or err or f"Тёмная тема {'включена' if enabled else 'выключена'}")

    if action == "power_profile":
        if not shutil.which("powerprofilesctl"):
            return _result(False, "powerprofilesctl не установлен")
        profile = str(value or "balanced").strip().casefold()
        aliases = {
            "экономия": "power-saver",
            "энергосбережение": "power-saver",
            "power saver": "power-saver",
            "powersaver": "power-saver",
            "баланс": "balanced",
            "сбалансированный": "balanced",
            "производительность": "performance",
        }
        profile = aliases.get(profile, profile)
        if profile not in {"power-saver", "balanced", "performance"}:
            return _result(False, f"Неизвестный профиль питания: {profile}")
        rc, out, err = self._run(["powerprofilesctl", "set", profile])
        return _result(rc == 0, out or err or f"Профиль питания: {profile}")

    if action == "screen_timeout":
        if not shutil.which("gsettings"):
            return _result(False, "gsettings не найден")
        minutes = int(value or "0")
        if not 0 <= minutes <= 240:
            return _result(False, "screen_timeout должен быть от 0 до 240 минут")
        rc, out, err = self._run([
            "gsettings", "set", "org.gnome.desktop.session", "idle-delay",
            f"uint32 {minutes * 60}",
        ])
        return _result(rc == 0, out or err or f"Таймаут экрана: {minutes} мин")

    if action == "do_not_disturb":
        if not shutil.which("gsettings"):
            return _result(False, "gsettings не найден")
        enabled = _as_bool(value)
        rc, out, err = self._run([
            "gsettings", "set", "org.gnome.desktop.notifications", "show-banners",
            "false" if enabled else "true",
        ])
        return _result(rc == 0, out or err or f"Не беспокоить {'включён' if enabled else 'выключен'}")

    if action == "mic_mute":
        muted = _as_bool(value)
        if shutil.which("wpctl"):
            cmd = ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1" if muted else "0"]
        elif shutil.which("pactl"):
            cmd = ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "1" if muted else "0"]
        else:
            return _result(False, "Не найден wpctl/pactl")
        rc, out, err = self._run(cmd)
        return _result(rc == 0, out or err or f"Микрофон {'выключен' if muted else 'включён'}")

    if action == "open_settings":
        if not shutil.which("gnome-control-center"):
            return _result(False, "gnome-control-center не установлен")
        panel = str(value or "").strip().casefold()
        panels = {
            "wifi": "wifi", "wi-fi": "wifi", "вайфай": "wifi",
            "bluetooth": "bluetooth", "блютуз": "bluetooth",
            "звук": "sound", "sound": "sound",
            "экран": "display", "display": "display",
            "питание": "power", "power": "power",
            "клавиатура": "keyboard", "keyboard": "keyboard",
            "мышь": "mouse", "mouse": "mouse",
            "сеть": "network", "network": "network",
            "уведомления": "notifications", "notifications": "notifications",
            "": "",
        }
        panel = panels.get(panel, panel)
        cmd = ["gnome-control-center"] + ([panel] if panel else [])
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _result(True, f"Открываю системные настройки{': ' + panel if panel else ''}")

    return _result(False, f"Неизвестное действие system_control: {action}")


def install_system_tools() -> None:
    """Extend ToolRegistry without giving the whole agent root privileges."""
    if getattr(ToolRegistry, "_stas_system_tools_installed", False):
        return

    original_init = ToolRegistry.__init__
    original_schemas = ToolRegistry.schemas

    def patched_init(self, config):
        original_init(self, config)
        self._tools["system_control"] = self.system_control
        # Replace the old launcher with the packaging-aware implementation.
        self._tools["open_application"] = self.open_application

    def patched_schemas(self):
        schemas = original_schemas(self)
        schemas.append({
            "type": "function",
            "function": {
                "name": "system_control",
                "description": (
                    "Изменить обычную пользовательскую системную настройку Ubuntu/Linux без sudo. "
                    "Используй для Wi-Fi, Bluetooth, ночного света, тёмной темы, режима питания, "
                    "таймаута экрана, режима 'не беспокоить', mute микрофона и открытия панели настроек. "
                    "Не говори, что нет прав, пока не попробовал этот tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "wifi", "bluetooth", "night_light", "dark_mode",
                                "power_profile", "screen_timeout", "do_not_disturb",
                                "mic_mute", "open_settings",
                            ],
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "on/off; true/false; power-saver/balanced/performance; "
                                "число минут; или имя панели настроек"
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        })
        return schemas

    ToolRegistry.system_control = _system_control
    ToolRegistry.open_application = _robust_open_application
    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry._stas_system_tools_installed = True
