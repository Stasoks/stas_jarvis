from __future__ import annotations

import json
import re
import shutil

from rapidfuzz.fuzz import WRatio

from .tools import ToolRegistry


def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)


def _norm(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"[^0-9a-zа-я.+_-]+", " ", text)
    return " ".join(text.split())


# Человеческие/голосовые варианты -> каноническая группа приложения.
# Здесь специально есть русские транслитерации брендов: Whisper вполне может
# вернуть «Хромиум», хотя WM_CLASS/title у окна написаны латиницей.
INPUT_ALIASES = {
    "браузер": "browser",
    "веб браузер": "browser",
    "web browser": "browser",
    "firefox": "firefox",
    "файрфокс": "firefox",
    "chrome": "chrome",
    "google chrome": "chrome",
    "гугл хром": "chrome",
    "хром": "chrome",
    "chromium": "chromium",
    "chromium browser": "chromium",
    "web browser chromium": "chromium",
    "веб браузер chromium": "chromium",
    "хромиум": "chromium",
    "хромиум браузер": "chromium",
    "браузер хромиум": "chromium",
    "brave": "brave",
    "брейв": "brave",
    "opera": "opera",
    "опера": "opera",
    "visual studio code": "vscode",
    "vs code": "vscode",
    "vscode": "vscode",
    "вс код": "vscode",
    "телеграм": "telegram",
    "telegram": "telegram",
    "discord": "discord",
    "дискорд": "discord",
    "spotify": "spotify",
    "спотифай": "spotify",
    "steam": "steam",
    "стим": "steam",
    "terminal": "terminal",
    "терминал": "terminal",
}

PATTERNS = {
    "browser": [
        "firefox", "google-chrome", "google chrome", "chrome", "chromium",
        "web-browser chromium", "web browser chromium", "brave", "opera",
        "yandex", "edge", "microsoft-edge",
    ],
    "firefox": ["firefox", "mozilla firefox"],
    "chrome": ["google-chrome", "google chrome", "chrome"],
    "chromium": [
        "chromium", "chromium-browser", "chromium browser",
        "web-browser chromium", "web browser chromium",
        "веб-браузер chromium", "веб браузер chromium",
    ],
    "brave": ["brave", "brave-browser"],
    "opera": ["opera"],
    "vscode": ["code.code", "visual studio code", "vscode", "code"],
    "telegram": ["telegram", "telegramdesktop", "telegram-desktop"],
    "discord": ["discord"],
    "spotify": ["spotify"],
    "steam": ["steam"],
    "terminal": ["gnome-terminal", "org.gnome.terminal", "kgx", "terminal"],
}


def _canonical(name: str) -> str:
    key = _norm(name)
    if key in INPUT_ALIASES:
        return INPUT_ALIASES[key]

    # Голос часто добавляет «веб-браузер», «окно», «приложение» и т.п.
    stripped = re.sub(
        r"\b(?:окно|приложение|активное|веб|web|браузер|browser)\b",
        " ",
        key,
    )
    stripped = " ".join(stripped.split())
    if stripped in INPUT_ALIASES:
        return INPUT_ALIASES[stripped]

    # Ищем длинный известный alias внутри естественной фразы.
    for alias in sorted(INPUT_ALIASES, key=len, reverse=True):
        if alias and alias in key:
            return INPUT_ALIASES[alias]
    return key


def _patterns(name: str) -> tuple[str, list[str]]:
    canonical = _canonical(name)
    pats = PATTERNS.get(canonical, [canonical])
    return canonical, [_norm(p) for p in pats if p]


def _score_window(patterns: list[str], wm_class: str, title: str) -> int:
    wc = _norm(wm_class)
    tt = _norm(title)
    combined = f"{wc} {tt}".strip()
    best = 0

    for p in patterns:
        if not p:
            continue
        score = 0
        if p == wc:
            score += 180
        if p in wc:
            score += 140
        if p in tt:
            score += 100
        if p in combined:
            score += 60
        score += int(WRatio(p, combined) * 0.55)
        best = max(best, score)

    return best


def _focus_application(self: ToolRegistry, name: str) -> str:
    canonical, patterns = _patterns(name)
    candidates = []

    if shutil.which("wmctrl"):
        rc, out, err = self._run(["wmctrl", "-lx"])
        if rc == 0 and out:
            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 4:
                    continue
                wid = parts[0]
                wm_class = parts[3]
                title = parts[4] if len(parts) >= 5 else ""
                score = _score_window(patterns, wm_class, title)
                candidates.append((score, wid, wm_class, title))

            candidates.sort(key=lambda x: x[0], reverse=True)
            if candidates and candidates[0][0] >= 70:
                score, wid, wm_class, title = candidates[0]
                rc2, out2, err2 = self._run(["wmctrl", "-ia", wid])
                if rc2 == 0:
                    return _result(
                        True,
                        f"Переключаюсь на {name}",
                        canonical=canonical,
                        score=score,
                        window_id=wid,
                        wm_class=wm_class,
                        title=title,
                    )

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
                            canonical=canonical,
                            score=score,
                            window_id=wid,
                            wm_class=wm_class,
                            title=title,
                        )

    # Fallback на xdotool. Здесь уже используем канонические латинские aliases,
    # поэтому «Хромиум» превращается в chromium до поиска окна.
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
                    return _result(
                        True,
                        f"Переключаюсь на {name}",
                        canonical=canonical,
                        matched=p,
                        window_id=wid,
                    )

    top = [
        {"score": s, "wm_class": c, "title": t}
        for s, _, c, t in candidates[:5]
        if t or c
    ]
    return _result(
        False,
        (
            f"Не нашёл окно для '{name}' (canonical={canonical}). "
            "Если это native Wayland-окно, wmctrl/xdotool могут его не видеть."
        ),
        candidates=top,
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
                    "Понимает русские/английские названия и варианты вроде Хромиум/Chromium, "
                    "Веб-браузер Chromium, VS Code, Telegram. Используй вместо ручного xdotool/grep."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
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
