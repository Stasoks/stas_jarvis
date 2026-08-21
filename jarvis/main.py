from __future__ import annotations

from .logging_setup import setup_logging


def main():
    setup_logging()

    # Расширяем базовый ToolRegistry безопасными системными настройками и
    # packaging-aware запуском приложений без выдачи root всему агенту.
    from .system_tools import install_system_tools
    install_system_tools()

    from .tui import JarvisApp

    # Очевидные desktop-команды (VS Code, Telegram, громкость, яркость,
    # media controls, Wi-Fi/Bluetooth и т.д.) выполняются локально, без LLM.
    from .fast_actions import install_fast_actions
    install_fast_actions(JarvisApp)

    JarvisApp().run()


if __name__ == "__main__":
    main()
