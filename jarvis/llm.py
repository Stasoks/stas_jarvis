from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, config_store):
        self.config_store = config_store
        self.last_provider_used = config_store.active_provider_name
        self.last_usage: dict[str, int] = {}
        self.total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    @property
    def provider_name(self) -> str:
        return self.config_store.active_provider_name

    @property
    def model(self) -> str:
        return self.config_store.provider["model"]

    def _provider(self, name: str) -> dict:
        return self.config_store.data["providers"][name]

    def _headers(self, provider_name: str, provider: dict) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.config_store.provider_key(provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if provider_name == "openrouter":
            headers["HTTP-Referer"] = "https://localhost/stas-jarvis"
            headers["X-Title"] = "Stas Jarvis"
        return headers

    def _record_usage(self, provider_name: str, model: str, data: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))

        self.last_usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }
        self.total_usage["prompt_tokens"] += prompt
        self.total_usage["completion_tokens"] += completion
        self.total_usage["total_tokens"] += total

        log.info(
            "LLM usage provider=%s model=%s prompt=%d completion=%d total=%d session_total=%d",
            provider_name,
            model,
            prompt,
            completion,
            total,
            self.total_usage["total_tokens"],
        )

    def _chat_once(
        self,
        provider_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        p = self._provider(provider_name)
        url = p["base_url"].rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": p["model"],
            "messages": messages,
            "stream": False,
            "max_tokens": int(p.get("max_tokens", 800)),
            "temperature": float(p.get("temperature", 0.2)),
        }
        if tools:
            payload["tools"] = tools

        timeout = float(p.get("timeout_sec", 120))
        log.info(
            "LLM request provider=%s model=%s messages=%d tools=%d",
            provider_name,
            p["model"],
            len(messages),
            len(tools or []),
        )

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.post(
                url,
                headers=self._headers(provider_name, p),
                json=payload,
            )
            if r.status_code >= 400:
                body = r.text[:2000]
                log.error("LLM HTTP %s provider=%s: %s", r.status_code, provider_name, body)
                if r.status_code == 429:
                    raise RuntimeError(
                        f"{provider_name}: HTTP 429 Too Many Requests. "
                        "Модель/провайдер сейчас rate-limited."
                    )
                r.raise_for_status()
            data = r.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM вернула ответ без choices: {data}")

        self._record_usage(provider_name, p["model"], data)
        self.last_provider_used = provider_name
        return choices[0]["message"]

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        active = self.provider_name
        fallback = self.config_store.data.get("fallback_provider")

        candidates = [active]
        if (
            fallback
            and fallback != active
            and fallback in self.config_store.data.get("providers", {})
        ):
            candidates.append(fallback)

        errors = []
        for i, name in enumerate(candidates):
            try:
                msg = self._chat_once(name, messages, tools)
                if i > 0:
                    log.warning("LLM failover: %s -> %s", active, name)
                return msg
            except Exception as e:
                errors.append(f"{name}: {e}")
                log.warning("Provider %s failed: %s", name, e)

        raise RuntimeError(" | ".join(errors))

    def list_models(self, contains: str = "", limit: int = 40) -> list[str]:
        p = self.config_store.provider
        name = self.provider_name
        url = p["base_url"].rstrip("/") + "/models"
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.get(url, headers=self._headers(name, p))
            r.raise_for_status()
            data = r.json()

        models = [x.get("id", "") for x in data.get("data", []) if x.get("id")]
        if contains:
            needle = contains.casefold()
            models = [m for m in models if needle in m.casefold()]
        return models[:limit]
