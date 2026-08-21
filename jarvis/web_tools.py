from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from .tools import ToolRegistry

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _result(ok: bool, message: str, **extra) -> str:
    return json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False)


def _clean_text(text: str) -> str:
    return " ".join((text or "").split())


def _public_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Разрешены только обычные http/https URL")

    host = parsed.hostname.casefold()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Локальные адреса запрещены для web_fetch")

    try:
        infos = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("Private/local IP запрещён для web_fetch")
    except socket.gaierror as exc:
        raise ValueError(f"Не удалось разрешить домен {host}") from exc

    return url


def _decode_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


def _decode_google_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/url?"):
        parsed = urlparse(href)
        target = parse_qs(parsed.query).get("q", [""])[0]
        if target:
            return unquote(target)
    return href


def _dedupe_results(results: list[dict], limit: int) -> list[dict]:
    out = []
    seen = set()
    for item in results:
        url = (item.get("url") or "").strip()
        title = _clean_text(item.get("title") or "")
        snippet = _clean_text(item.get("snippet") or "")
        if not url.startswith(("http://", "https://")) or not title:
            continue
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def _search_duckduckgo(client: httpx.Client, query: str, limit: int) -> list[dict]:
    # DuckDuckGo's HTML endpoint expects a form-style search. GET often returns
    # a shell/empty page, which was the reason the old implementation failed.
    response = client.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "wt-wt"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []
    for block in soup.select(".result"):
        a = block.select_one("a.result__a")
        if not a:
            continue
        title = _clean_text(a.get_text(" ", strip=True))
        url = _decode_ddg_url(a.get("href", ""))
        snippet_node = block.select_one(".result__snippet")
        snippet = _clean_text(
            snippet_node.get_text(" ", strip=True) if snippet_node else ""
        )
        results.append({"title": title, "url": url, "snippet": snippet})

    # DDG occasionally serves the lite-ish markup even from the html endpoint.
    if not results:
        for a in soup.select("a[href]"):
            title = _clean_text(a.get_text(" ", strip=True))
            url = _decode_ddg_url(a.get("href", ""))
            if (
                len(title) >= 3
                and url.startswith(("http://", "https://"))
                and "duckduckgo.com" not in urlparse(url).netloc
            ):
                results.append({"title": title, "url": url, "snippet": ""})

    return _dedupe_results(results, limit)


def _search_bing(client: httpx.Client, query: str, limit: int) -> list[dict]:
    response = client.get(
        "https://www.bing.com/search",
        params={"q": query, "count": max(limit, 10), "setlang": "ru"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []
    for block in soup.select("li.b_algo"):
        a = block.select_one("h2 a")
        if not a:
            continue
        title = _clean_text(a.get_text(" ", strip=True))
        url = a.get("href", "")
        snippet_node = block.select_one(".b_caption p") or block.select_one("p")
        snippet = _clean_text(
            snippet_node.get_text(" ", strip=True) if snippet_node else ""
        )
        results.append({"title": title, "url": url, "snippet": snippet})

    return _dedupe_results(results, limit)


def _search_google(client: httpx.Client, query: str, limit: int) -> list[dict]:
    response = client.get(
        "https://www.google.com/search",
        params={"q": query, "num": max(limit, 10), "hl": "ru"},
        headers={**_HEADERS, "Cookie": "CONSENT=YES+cb"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []

    # Current desktop layout.
    for block in soup.select("div.MjjYud"):
        a = block.select_one("a[href]")
        h = block.select_one("h3")
        if not a or not h:
            continue
        title = _clean_text(h.get_text(" ", strip=True))
        url = _decode_google_url(a.get("href", ""))
        snippet_node = block.select_one("div.VwiC3b")
        snippet = _clean_text(
            snippet_node.get_text(" ", strip=True) if snippet_node else ""
        )
        results.append({"title": title, "url": url, "snippet": snippet})

    # More generic fallback for changed Google markup.
    if not results:
        for h in soup.find_all("h3"):
            a = h.find_parent("a")
            if not a:
                continue
            title = _clean_text(h.get_text(" ", strip=True))
            url = _decode_google_url(a.get("href", ""))
            results.append({"title": title, "url": url, "snippet": ""})

    return _dedupe_results(results, limit)


def _web_search(self: ToolRegistry, query: str, max_results: int = 8) -> str:
    """Search the public web in background without opening a GUI browser."""
    query = query.strip()
    if not query:
        return _result(False, "Пустой поисковый запрос")

    limit = max(1, min(int(max_results), 12))
    errors: list[str] = []

    with httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:
        backends = (
            ("duckduckgo", _search_duckduckgo),
            ("bing", _search_bing),
            ("google", _search_google),
        )

        for backend_name, backend in backends:
            try:
                results = backend(client, query, limit)
                if results:
                    log.info(
                        "BACKGROUND WEB search backend=%s query=%r results=%d",
                        backend_name,
                        query,
                        len(results),
                    )
                    return _result(
                        True,
                        f"Найдено результатов: {len(results)}",
                        query=query,
                        results=results,
                        backend=backend_name,
                        mode="background_http",
                    )

                errors.append(f"{backend_name}: выдача пуста")
                log.warning(
                    "Background search backend %s returned no results for %r",
                    backend_name,
                    query,
                )
            except Exception as exc:
                err = f"{backend_name}: {type(exc).__name__}: {exc}"
                errors.append(err)
                log.warning("Background search failed: %s", err)

    return _result(
        False,
        "Фоновый поиск не удался во всех поисковиках",
        query=query,
        errors=errors,
    )


def _web_fetch(self: ToolRegistry, url: str, max_chars: int = 12000) -> str:
    """Fetch and extract readable page text without starting Playwright/GUI."""
    try:
        safe_url = _public_http_url(url.strip())
        limit = max(1000, min(int(max_chars), 20000))

        with httpx.Client(
            timeout=25,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            response = client.get(safe_url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").casefold()
        final_url = str(response.url)

        if (
            "text/html" in content_type
            or "application/xhtml" in content_type
            or not content_type
        ):
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(
                ["script", "style", "noscript", "svg", "canvas", "template"]
            ):
                tag.decompose()

            title = _clean_text(
                soup.title.get_text(" ", strip=True) if soup.title else ""
            )
            root = soup.find("article") or soup.find("main") or soup.body or soup
            text = root.get_text("\n", strip=True)
        elif content_type.startswith("text/") or "json" in content_type:
            title = ""
            text = response.text
        else:
            return _result(
                False,
                f"Неподдерживаемый Content-Type: {content_type or 'unknown'}",
                url=final_url,
            )

        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        truncated = len(text) > limit
        text = text[:limit]

        log.info(
            "BACKGROUND WEB fetch url=%s chars=%d truncated=%s",
            final_url,
            len(text),
            truncated,
        )
        return _result(
            True,
            text,
            url=final_url,
            title=title,
            truncated=truncated,
            mode="background_http",
        )
    except Exception as exc:
        log.warning("Background web fetch failed for %s: %s", url, exc)
        return _result(
            False,
            f"Не удалось прочитать страницу: {type(exc).__name__}: {exc}",
        )


def install_web_tools() -> None:
    if getattr(ToolRegistry, "_stas_web_tools_installed", False):
        return

    original_init = ToolRegistry.__init__
    original_schemas = ToolRegistry.schemas

    def patched_init(self, config):
        original_init(self, config)
        self._tools["web_search"] = self.web_search
        self._tools["web_fetch"] = self.web_fetch

    def patched_schemas(self):
        schemas = original_schemas(self)
        schemas.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": (
                            "Фоновый поиск информации в интернете БЕЗ открытия окна браузера. "
                            "Используй для исследований, свежих фактов, новостей, моделей, "
                            "бенчмарков и обычного веб-поиска. Внутри автоматически пробуются "
                            "несколько поисковиков. Возвращает заголовки, URL и snippets."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "max_results": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 12,
                                },
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "web_fetch",
                        "description": (
                            "Фоново скачать и прочитать текст публичной веб-страницы по URL БЕЗ GUI. "
                            "Используй после web_search, чтобы проверить источник и извлечь детали."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "max_chars": {
                                    "type": "integer",
                                    "minimum": 1000,
                                    "maximum": 20000,
                                },
                            },
                            "required": ["url"],
                            "additionalProperties": False,
                        },
                    },
                },
            ]
        )
        return schemas

    ToolRegistry.web_search = _web_search
    ToolRegistry.web_fetch = _web_fetch
    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry._stas_web_tools_installed = True
