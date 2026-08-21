from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path.home() / ".config" / "stas-jarvis"
CONFIG_PATH = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / "stas-jarvis"
LOG_PATH = DATA_DIR / "jarvis.log"
SESSIONS_DIR = DATA_DIR / "sessions"

load_dotenv(PROJECT_ROOT / ".env")

def _expand(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("~"):
        return str(Path(value).expanduser())
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value

class ConfigStore:
    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        if not CONFIG_PATH.exists():
            example = PROJECT_ROOT / "config.example.json"
            CONFIG_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

        self.data = self.load()

    def load(self) -> dict:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return _expand(raw)

    def reload(self) -> None:
        self.data = self.load()

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def active_provider_name(self) -> str:
        return self.data["active_provider"]

    @property
    def provider(self) -> dict:
        return deepcopy(self.data["providers"][self.active_provider_name])

    def provider_key(self, provider: dict | None = None) -> str:
        provider = provider or self.provider
        env_name = provider.get("api_key_env", "")
        return os.getenv(env_name, "") if env_name else provider.get("api_key", "")

    def set_provider(self, name: str) -> None:
        if name not in self.data["providers"]:
            raise KeyError(f"Неизвестный provider: {name}")
        self.data["active_provider"] = name
        self.save()

    def set_model(self, model: str) -> None:
        self.data["providers"][self.active_provider_name]["model"] = model
        self.save()
