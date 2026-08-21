from __future__ import annotations

import base64
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import httpx
from PIL import Image, ImageGrab

from .tools import ToolRegistry

log = logging.getLogger(__name__)


def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)


class ScreenVision:
    """Capture the current desktop and ask a vision-capable model about it.

    Screenshots are taken only on demand. They are downscaled/compressed before
    upload so a simple "what is on my screen?" does not become a token bonfire.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.data_dir = Path.home() / ".local" / "share" / "stas-jarvis" / "screens"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def vision_cfg(self) -> dict[str, Any]:
        return self.config.get("vision", {})

    def _capture_png(self) -> Path:
        raw = self.data_dir / "latest_raw.png"
        raw.unlink(missing_ok=True)

        commands: list[list[str]] = []
        if shutil.which("gnome-screenshot"):
            commands.append(["gnome-screenshot", "-f", str(raw)])
        if shutil.which("grim"):
            commands.append(["grim", str(raw)])
        if shutil.which("scrot"):
            commands.append(["scrot", str(raw)])

        for cmd in commands:
            try:
                cp = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if cp.returncode == 0 and raw.exists() and raw.stat().st_size > 0:
                    return raw
                log.warning("Screenshot command failed: %s | %s", cmd, cp.stderr[-500:])
            except Exception:
                log.exception("Screenshot command crashed: %s", cmd)

        # Pillow can work on X11 and on some desktop setups through helpers.
        try:
            image = ImageGrab.grab(all_screens=True)
            image.save(raw, "PNG")
            if raw.exists() and raw.stat().st_size > 0:
                return raw
        except Exception as exc:
            raise RuntimeError(
                "Не удалось сделать скриншот. Установи gnome-screenshot "
                "или используй поддерживаемую X11/Wayland-сессию."
            ) from exc

        raise RuntimeError("Не удалось получить изображение экрана")

    def _prepare_jpeg(self, raw: Path) -> tuple[Path, int, int]:
        out = self.data_dir / "latest.jpg"
        max_side = int(self.vision_cfg.get("max_image_side", 1280))
        quality = int(self.vision_cfg.get("jpeg_quality", 70))
        max_side = max(640, min(1920, max_side))
        quality = max(45, min(90, quality))

        with Image.open(raw) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            width, height = image.size
            image.save(out, "JPEG", quality=quality, optimize=True)

        return out, width, height

    def _provider(self) -> tuple[str, dict[str, Any], str]:
        providers = self.config.get("providers", {})
        vcfg = self.vision_cfg

        requested = str(vcfg.get("provider") or "").strip()
        if not requested:
            requested = "openrouter" if "openrouter" in providers else self.config.get("active_provider", "")
        if requested not in providers:
            raise RuntimeError(f"Vision provider '{requested}' не найден в config.json")

        provider = providers[requested]
        model = str(vcfg.get("model") or "").strip()
        if not model:
            # OpenRouter's free router filters for image-understanding support
            # when an image is present in the request.
            model = "openrouter/free" if requested == "openrouter" else str(provider.get("model", ""))
        if not model:
            raise RuntimeError("Не настроена vision-модель")

        return requested, provider, model

    def _headers(self, provider_name: str, provider: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        env_name = str(provider.get("api_key_env") or "")
        key = os.getenv(env_name, "") if env_name else str(provider.get("api_key") or "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if provider_name == "openrouter":
            headers["HTTP-Referer"] = "https://localhost/stas-jarvis"
            headers["X-Title"] = "Stas Jarvis Screen Vision"
        return headers

    def analyze(self, question: str) -> tuple[str, dict[str, Any]]:
        if self.vision_cfg.get("enabled", True) is False:
            raise RuntimeError("Screen vision отключён в config.json")

        raw = self._capture_png()
        jpeg, width, height = self._prepare_jpeg(raw)
        encoded = base64.b64encode(jpeg.read_bytes()).decode("ascii")

        provider_name, provider, model = self._provider()
        url = str(provider["base_url"]).rstrip("/") + "/chat/completions"

        prompt = question.strip() or "Кратко опиши, что сейчас видно на экране и какие важные элементы интерфейса доступны."
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты модуль компьютерного зрения desktop-агента. Анализируй только текущий скриншот. "
                        "Отвечай по-русски, конкретно. Если пользователь спрашивает про кнопку, окно, ошибку или "
                        "элемент интерфейса, укажи где он находится словами. Не выдумывай невидимые элементы."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                },
            ],
            "max_tokens": int(self.vision_cfg.get("max_tokens", 450)),
            "temperature": 0.1,
            "stream": False,
        }

        timeout = float(provider.get("timeout_sec", 120))
        log.info(
            "SCREEN VISION request provider=%s model=%s image=%dx%d bytes=%d",
            provider_name, model, width, height, jpeg.stat().st_size,
        )

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.post(url, headers=self._headers(provider_name, provider), json=payload)
            if r.status_code >= 400:
                body = r.text[:2000]
                log.error("SCREEN VISION HTTP %s: %s", r.status_code, body)
                raise RuntimeError(f"Vision API HTTP {r.status_code}: {body[:500]}")
            data = r.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Vision-модель вернула ответ без choices: {data}")

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
            content = "\n".join(parts)

        answer = str(content).strip()
        if not answer:
            raise RuntimeError("Vision-модель вернула пустой ответ")

        usage = data.get("usage") or {}
        log.info(
            "SCREEN VISION usage provider=%s model=%s prompt=%s completion=%s total=%s",
            provider_name,
            model,
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
            usage.get("total_tokens", "?"),
        )
        return answer, {
            "provider": provider_name,
            "model": model,
            "width": width,
            "height": height,
            "usage": usage,
        }


def _see_screen(self: ToolRegistry, question: str = "Что сейчас видно на экране?") -> str:
    try:
        answer, meta = self._screen_vision.analyze(question)
        return _result(True, answer, **meta)
    except Exception as exc:
        log.exception("see_screen failed")
        return _result(False, str(exc))


def install_screen_tools() -> None:
    if getattr(ToolRegistry, "_stas_screen_tools_installed", False):
        return

    original_init = ToolRegistry.__init__
    original_schemas = ToolRegistry.schemas

    def patched_init(self, config):
        original_init(self, config)
        self._screen_vision = ScreenVision(config)
        self._tools["see_screen"] = self.see_screen

    def patched_schemas(self):
        schemas = original_schemas(self)
        schemas.append({
            "type": "function",
            "function": {
                "name": "see_screen",
                "description": (
                    "Сделать скриншот текущего рабочего стола и визуально проанализировать его. "
                    "Используй, когда запрос зависит от того, что прямо сейчас видно на экране: "
                    "какое окно открыто, какая ошибка показана, где находится кнопка, что изображено, "
                    "или когда пользователь прямо просит посмотреть на экран. Скриншот делается только по вызову."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Что именно нужно определить по текущему экрану",
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        })
        return schemas

    ToolRegistry.see_screen = _see_screen
    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry._stas_screen_tools_installed = True
