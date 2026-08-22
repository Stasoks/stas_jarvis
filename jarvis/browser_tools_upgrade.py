from __future__ import annotations

from .tools import ToolRegistry


def install_browser_tools_upgrade() -> None:
    if getattr(ToolRegistry, "_stas_browser_upgrade_installed", False):
        return

    original_init = ToolRegistry.__init__
    original_schemas = ToolRegistry.schemas

    def patched_init(self, config):
        original_init(self, config)
        self._tools.update({
            "browser_observe": self.browser_observe,
            "browser_press": self.browser_press,
            "browser_scroll": self.browser_scroll,
            "browser_wait": self.browser_wait,
            "browser_tabs": self.browser_tabs,
            "browser_select_tab": self.browser_select_tab,
            "browser_download": self.browser_download,
        })

    def patched_schemas(self):
        schemas = original_schemas(self)
        schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "browser_observe",
                    "description": (
                        "Получить текущее состояние видимого браузера: URL, заголовок, текст страницы и стабильные refs "
                        "интерактивных элементов. Для browser-задач вызывай это после каждого существенного перехода/клика."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 14000},
                            "limit": {"type": "integer", "minimum": 10, "maximum": 150},
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_press",
                    "description": "Нажать клавишу/сочетание в активной вкладке Playwright, например Enter, Escape, Control+L.",
                    "parameters": {
                        "type": "object",
                        "properties": {"keys": {"type": "string"}},
                        "required": ["keys"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_scroll",
                    "description": "Прокрутить активную веб-страницу. Положительное amount вниз, отрицательное вверх.",
                    "parameters": {
                        "type": "object",
                        "properties": {"amount": {"type": "integer", "minimum": -3000, "maximum": 3000}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_wait",
                    "description": "Подождать загрузку/анимацию страницы несколько секунд.",
                    "parameters": {
                        "type": "object",
                        "properties": {"seconds": {"type": "number", "minimum": 0.1, "maximum": 10}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_tabs",
                    "description": "Показать вкладки управляемого браузера.",
                    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_select_tab",
                    "description": "Переключить активную вкладку управляемого браузера по индексу.",
                    "parameters": {
                        "type": "object",
                        "properties": {"index": {"type": "integer", "minimum": 0}},
                        "required": ["index"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_download",
                    "description": (
                        "Кликнуть элемент по ref и дождаться браузерной загрузки. Файл сохраняется в ~/Downloads. "
                        "Перед вызовом обязательно browser_observe, чтобы получить актуальный ref."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"ref": {"type": "string"}},
                        "required": ["ref"],
                        "additionalProperties": False,
                    },
                },
            },
        ])
        return schemas

    def browser_observe(self, max_chars: int = 7000, limit: int = 90):
        return self.browser.observe(max_chars=max_chars, limit=limit)

    def browser_press(self, keys: str):
        return self.browser.press(keys)

    def browser_scroll(self, amount: int = 650):
        return self.browser.scroll(amount)

    def browser_wait(self, seconds: float = 1.0):
        return self.browser.wait(seconds)

    def browser_tabs(self):
        return self.browser.tabs()

    def browser_select_tab(self, index: int):
        return self.browser.select_tab(index)

    def browser_download(self, ref: str):
        return self.browser.download(ref)

    ToolRegistry.__init__ = patched_init
    ToolRegistry.schemas = patched_schemas
    ToolRegistry.browser_observe = browser_observe
    ToolRegistry.browser_press = browser_press
    ToolRegistry.browser_scroll = browser_scroll
    ToolRegistry.browser_wait = browser_wait
    ToolRegistry.browser_tabs = browser_tabs
    ToolRegistry.browser_select_tab = browser_select_tab
    ToolRegistry.browser_download = browser_download
    ToolRegistry._stas_browser_upgrade_installed = True
