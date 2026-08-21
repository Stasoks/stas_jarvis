from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

class BrowserManager:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._refs: dict[str, dict[str, Any]] = {}

    def _ensure(self):
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale="ru-RU",
            viewport={"width": 1280, "height": 900},
        )
        self._page = self._context.new_page()

    @property
    def page(self):
        self._ensure()
        return self._page

    def open(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Открыто: {self.page.title()} | {self.page.url}"

    def search(self, query: str) -> str:
        return self.open("https://duckduckgo.com/?q=" + quote_plus(query))

    def read(self, max_chars: int = 12000) -> str:
        self._ensure()
        text = self.page.locator("body").inner_text(timeout=10000)
        return f"TITLE: {self.page.title()}\nURL: {self.page.url}\n\n{text[:max_chars]}"

    def elements(self, limit: int = 80) -> str:
        self._ensure()
        items = self.page.evaluate(
            """
            () => {
              const els = [...document.querySelectorAll(
                'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'
              )];
              return els.map((el, i) => ({
                i,
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.value || el.getAttribute('aria-label') ||
                       el.getAttribute('placeholder') || el.title || '').trim().slice(0,160),
                href: el.href || '',
                type: el.type || '',
                aria: el.getAttribute('aria-label') || '',
                placeholder: el.getAttribute('placeholder') || ''
              })).filter(x => x.text || x.href);
            }
            """
        )
        self._refs.clear()
        lines = []
        for idx, item in enumerate(items[:limit], start=1):
            ref = f"e{idx}"
            self._refs[ref] = item
            desc = item["text"] or item["href"]
            lines.append(f"{ref}: <{item['tag']}> {desc[:180]}")
        return "\n".join(lines) if lines else "Интерактивных элементов не найдено."

    def _locator_for_ref(self, ref: str):
        if ref not in self._refs:
            raise ValueError("Неизвестный ref. Сначала вызови browser_list_elements.")
        item = self._refs[ref]
        return self.page.locator(
            "a,button,input,textarea,select,[role='button'],[role='link'],[contenteditable='true']"
        ).nth(int(item["i"]))

    def click(self, ref: str) -> str:
        loc = self._locator_for_ref(ref)
        loc.click(timeout=10000)
        self.page.wait_for_timeout(500)
        return f"Клик по {ref} выполнен. URL: {self.page.url}"

    def fill(self, ref: str, text: str, press_enter: bool = False) -> str:
        loc = self._locator_for_ref(ref)
        try:
            loc.fill(text, timeout=10000)
        except Exception:
            loc.click(timeout=5000)
            self.page.keyboard.type(text)
        if press_enter:
            loc.press("Enter")
            self.page.wait_for_timeout(800)
        return f"Поле {ref} заполнено."

    def back(self) -> str:
        self.page.go_back(wait_until="domcontentloaded", timeout=30000)
        return f"Назад: {self.page.url}"

    def close(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = self._browser = self._context = self._page = None
