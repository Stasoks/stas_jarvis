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

    By default vision follows the currently active provider/model. If that
    provider cannot accept image input, a separately configured fallback may
    be used. Screenshots are taken only on demand.
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

    def _candidate_providers(self) -> list[tuple[str, dict[str, Any], str]]:
        providers = self.config.get("providers", {})
        vcfg = self.vision_cfg
        candidates: list[tuple[str, dict[str, Any], str]] = []
        seen: set[tuple[str, str]] = set()

        def add(provider_name: str, model: str = "") -> None:
            provider_name = str(provider_name or "").strip()
            if not provider_name or provider_name not in providers:
                return
            provider = providers[provider_name]
            resolved_model = str(model or provider.get("model") or "").strip()
            if not resolved_model:
                return
            key = (provider_name, resolved_model)
            if key in seen:
                return
            seen.add(key)
            candidates.append((provider_name, provider, resolved_model))

        # Default behavior: screen vision follows whatever model the user has
        # selected in the TUI. This is especially useful for local VL models in
        # LM Studio such as Qwen3-VL.
        if vcfg.get("follow_active_provider", True):
            active = str(self.config.get("active_provider") or "").strip()
            add(active)

        # Optional explicitly configured vision provider/model. This also
        # doubles as a fallback for existing configs that still contain
        # provider=openrouter/model=openrouter/free.
        requested = str(vcfg.get("provider") or "").strip()
        requested_model = str(vcfg.get("model") or "").strip()
        if requested and requested != "active":
            if not requested_model and requested == "openrouter":
                requested_model = "openrouter/free"
            add(requested, requested_model)

        fallback_provider = str(vcfg.get("fallback_provider") or "").strip()
        fallback_model = str(vcfg.get("fallback_model") or "").strip()
        if fallback_provider:
            if not fallback_model and fallback_provider == "openrouter":
                fallback_model = "openrouter/free"
            add(fallback_provider, fallback_model)

        # Last resort: retain the old behavior only if nothing else was found.
        if not candidates and "openrouter" in providers:
            add("openrouter", "openrouter/free")

        if not candidates:
            raise RuntimeError("Не удалось подобрать vision provider/model")

        return candidates

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

    def _request_one(
        self,
        provider_name: str,
        provider: dict[str, Any],
        model: str,
        encoded: str,
        width: int,
        height: int,
        image_bytes: int,
        prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        url = str(provider["base_url"]).rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты модуль компьютерного зрения desktop-агента. Анализируй именно текущий скриншот. "
                        "Отвечай по-русски и конкретно. Опиши видимые окна, текст, ошибки и элементы интерфейса, "
                        "которые относятся к вопросу пользователя. Если пользователь спрашивает про кнопку или "
                        "элемент интерфейса, укажи где он находится словами. Не выдумывай невидимые элементы. "
                        "Не отвечай классификацией безопасности вроде 'safe/unsafe': нужен визуальный анализ экрана."
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
            provider_name, model, width, height, image_bytes,
        )

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.post(url, headers=self._headers(provider_name, provider), json=payload)
            if r.status_code >= 400:
                body = r.text[:2000]
                raise RuntimeError(f"HTTP {r.status_code}: {body[:700]}")
            data = r.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"ответ без choices: {str(data)[:700]}")

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
            raise RuntimeError("vision-модель вернула пустой ответ")

        # A generic free router can occasionally pick a moderation/safety model.
        # Treat that as a bad vision answer and continue to the next candidate.
        low = answer.casefold().strip()
        suspicious = (
            len(answer) < 120
            and any(x in low for x in ("user safety", "safe.", "unsafe", "safety:"))
        )
        if suspicious:
            raise RuntimeError(f"модель вернула safety-классификацию вместо vision-ответа: {answer}")

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

    def analyze(self, question: str) -> tuple[str, dict[str, Any]]:
        if self.vision_cfg.get("enabled", True) is False:
            raise RuntimeError("Screen vision отключён в config.json")

        raw = self._capture_png()
        jpeg, width, height = self._prepare_jpeg(raw)
        encoded = base64.b64encode(jpeg.read_bytes()).decode("ascii")
        prompt = question.strip() or "Кратко опиши, что сейчас видно на экране и какие важные элементы интерфейса доступны."

        errors: list[str] = []
        for provider_name, provider, model in self._candidate_providers():
            try:
                return self._request_one(
                    provider_name,
                    provider,
                    model,
                    encoded,
                    width,
                    height,
                    jpeg.stat().st_size,
                    prompt,
                )
            except Exception as exc:
                msg = f"{provider_name}/{model}: {type(exc).__name__}: {exc}"
                errors.append(msg)
                log.warning("SCREEN VISION candidate failed: %s", msg)

        raise RuntimeError("Все vision-кандидаты провалились: " + " | ".join(errors))


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
