from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import quote_plus

log = logging.getLogger(__name__)


class BrowserManager:
    """Visible, persistent Playwright browser for interactive user tasks.

    Ordinary research uses web_search/web_fetch and never touches this class.
    This browser exists for requests where the user explicitly wants a site
    opened or interacted with.
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw = None
        self._context = None
        self._page = None
        self._refs: dict[str, dict[str, Any]] = {}
        self.profile_dir = Path.home() / ".local" / "share" / "stas-jarvis" / "browser-profile"
        self.download_dir = Path.home() / "Downloads"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def _system_browser(self) -> str | None:
        # Prefer the user's real Chromium/Chrome if present. Playwright's bundled
        # browser is still a fallback, but this makes the visible window feel
        # much less like a mysterious second browser.
        for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
            path = shutil.which(name)
            if path:
                return path
        return None

    def _ensure(self):
        if self._page is not None and not self._page.is_closed():
            return

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "locale": "ru-RU",
            "viewport": {"width": 1360, "height": 900},
            "accept_downloads": True,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        executable = self._system_browser()
        if executable:
            kwargs["executable_path"] = executable

        self._context = self._pw.chromium.launch_persistent_context(**kwargs)
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.bring_to_front()
        log.info("Interactive browser started executable=%s profile=%s", executable or "playwright", self.profile_dir)

    @property
    def page(self):
        self._ensure()
        return self._page

    def _active_page(self):
        self._ensure()
        pages = [p for p in self._context.pages if not p.is_closed()]
        if self._page not in pages:
            self._page = pages[-1] if pages else self._context.new_page()
        return self._page

    def open(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        p = self._active_page()
        p.bring_to_front()
        p.goto(url, wait_until="domcontentloaded", timeout=45000)
        return f"Открыто: {p.title()} | {p.url}"

    def search(self, query: str) -> str:
        # Visible browser search. Background factual search is handled elsewhere.
        return self.open("https://www.google.com/search?q=" + quote_plus(query))

    def read(self, max_chars: int = 12000) -> str:
        p = self._active_page()
        text = p.locator("body").inner_text(timeout=10000)
        return f"TITLE: {p.title()}\nURL: {p.url}\n\n{text[:max_chars]}"

    def observe(self, max_chars: int = 7000, limit: int = 90) -> str:
        """Return page state + stable refs in one call.

        Refs are written into DOM attributes so clicks are not based on a fragile
        nth() index that changes as ads or lazy-loaded nodes appear.
        """
        p = self._active_page()
        p.bring_to_front()

        items = p.evaluate(
            """
            ({limit}) => {
              document.querySelectorAll('[data-stas-ref]').forEach(el => el.removeAttribute('data-stas-ref'));
              const selector = [
                'a[href]', 'button', 'input', 'textarea', 'select',
                '[role="button"]', '[role="link"]', '[role="checkbox"]',
                '[role="radio"]', '[role="tab"]', '[role="menuitem"]',
                '[contenteditable="true"]', 'summary'
              ].join(',');
              const all = [...document.querySelectorAll(selector)];
              const visible = all.filter(el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none';
              }).slice(0, limit);

              return visible.map((el, i) => {
                const ref = `e${i + 1}`;
                el.setAttribute('data-stas-ref', ref);
                const r = el.getBoundingClientRect();
                const text = (
                  el.getAttribute('aria-label') || el.innerText || el.value ||
                  el.getAttribute('placeholder') || el.getAttribute('title') || ''
                ).trim().replace(/\s+/g, ' ').slice(0, 220);
                return {
                  ref,
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || '',
                  text,
                  href: el.href || '',
                  type: el.type || '',
                  disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
                  checked: el.checked ?? null,
                  x: Math.round(r.left + r.width / 2),
                  y: Math.round(r.top + r.height / 2),
                };
              });
            }
            """,
            {"limit": max(1, min(int(limit), 150))},
        )

        self._refs = {item["ref"]: item for item in items}
        try:
            body = p.locator("body").inner_text(timeout=8000)
        except Exception:
            body = ""
        body = " ".join(body.split())[:max(1000, min(int(max_chars), 14000))]

        state = {
            "title": p.title(),
            "url": p.url,
            "text": body,
            "elements": items,
        }
        return json.dumps(state, ensure_ascii=False)

    def elements(self, limit: int = 80) -> str:
        data = json.loads(self.observe(max_chars=1200, limit=limit))
        lines = []
        for item in data["elements"]:
            desc = item.get("text") or item.get("href") or item.get("role") or item.get("tag")
            suffix = f" -> {item['href']}" if item.get("href") else ""
            lines.append(f"{item['ref']}: <{item['tag']}> {desc[:180]}{suffix[:180]}")
        return "\n".join(lines) if lines else "Интерактивных элементов не найдено."

    def _locator_for_ref(self, ref: str):
        p = self._active_page()
        loc = p.locator(f'[data-stas-ref="{ref}"]').first
        if loc.count() == 0:
            raise ValueError("Ref устарел или исчез. Снова вызови browser_observe.")
        return loc

    def click(self, ref: str) -> str:
        p = self._active_page()
        loc = self._locator_for_ref(ref)
        loc.scroll_into_view_if_needed(timeout=8000)
        loc.click(timeout=12000)
        p.wait_for_timeout(650)
        return f"Клик по {ref} выполнен. URL: {p.url}"

    def fill(self, ref: str, text: str, press_enter: bool = False) -> str:
        p = self._active_page()
        loc = self._locator_for_ref(ref)
        loc.scroll_into_view_if_needed(timeout=8000)
        try:
            loc.fill(text, timeout=10000)
        except Exception:
            loc.click(timeout=5000)
            p.keyboard.press("Control+A")
            p.keyboard.type(text, delay=10)
        if press_enter:
            loc.press("Enter")
            p.wait_for_timeout(900)
        return f"Поле {ref} заполнено."

    def press(self, keys: str) -> str:
        p = self._active_page()
        p.keyboard.press(keys)
        p.wait_for_timeout(300)
        return f"Нажато в браузере: {keys}"

    def scroll(self, amount: int = 650) -> str:
        p = self._active_page()
        amount = max(-3000, min(3000, int(amount)))
        p.evaluate("dy => window.scrollBy({top: dy, behavior: 'instant'})", amount)
        p.wait_for_timeout(350)
        return f"Прокрутка браузера: {amount}px"

    def wait(self, seconds: float = 1.0) -> str:
        p = self._active_page()
        seconds = max(0.1, min(10.0, float(seconds)))
        p.wait_for_timeout(int(seconds * 1000))
        return f"Подождал {seconds:.1f}с. URL: {p.url}"

    def tabs(self) -> str:
        self._ensure()
        pages = [p for p in self._context.pages if not p.is_closed()]
        result = []
        for i, p in enumerate(pages):
            result.append({"index": i, "active": p is self._page, "title": p.title(), "url": p.url})
        return json.dumps(result, ensure_ascii=False)

    def select_tab(self, index: int) -> str:
        self._ensure()
        pages = [p for p in self._context.pages if not p.is_closed()]
        index = int(index)
        if index < 0 or index >= len(pages):
            raise ValueError(f"Нет вкладки с индексом {index}")
        self._page = pages[index]
        self._page.bring_to_front()
        return f"Активна вкладка {index}: {self._page.title()} | {self._page.url}"

    def download(self, ref: str) -> str:
        p = self._active_page()
        loc = self._locator_for_ref(ref)
        loc.scroll_into_view_if_needed(timeout=8000)
        with p.expect_download(timeout=30000) as info:
            loc.click(timeout=12000)
        download = info.value
        name = download.suggested_filename or "download.bin"
        target = self.download_dir / name
        # Don't silently overwrite an existing file.
        if target.exists():
            stem, suffix = target.stem, target.suffix
            i = 2
            while (self.download_dir / f"{stem}_{i}{suffix}").exists():
                i += 1
            target = self.download_dir / f"{stem}_{i}{suffix}"
        download.save_as(str(target))
        return f"Скачано: {target}"

    def back(self) -> str:
        p = self._active_page()
        p.go_back(wait_until="domcontentloaded", timeout=30000)
        return f"Назад: {p.url}"

    def close(self):
        try:
            if self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = self._context = self._page = None
            self._refs.clear()
