from __future__ import annotations

import json
import shutil
import subprocess

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
    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry._stas_system_tools_installed = True
