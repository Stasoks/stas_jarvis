from __future__ import annotations

from .logging_setup import setup_logging


def main():
    setup_logging()

    # Жёсткий wake-word guard.
    from .wake_guard import install_strict_wake_word
    install_strict_wake_word()

    # Обычные desktop/system tools.
    from .system_tools import install_system_tools
    install_system_tools()

    # Умный фокус уже открытых приложений.
    from .window_tools import install_window_tools
    install_window_tools()

    # Фоновый web_search/web_fetch. Никаких окон Chromium для обычного research.
    from .web_tools import install_web_tools
    install_web_tools()

    # Интерактивный persistent Playwright browser.
    from .browser_tools_upgrade import install_browser_tools_upgrade
    install_browser_tools_upgrade()

    # On-demand screenshot + vision.
    from .screen_tools import install_screen_tools
    install_screen_tools()

    # Полноценный computer-use слой поверх screenshot vision + mouse/keyboard.
    from .computer_tools import install_computer_tools
    install_computer_tools()

    from .tui import JarvisApp

    # Никаких regex fast-actions для обычных пользовательских команд.
    # Смысл запроса и выбор tools делает сам агент.

    # Прямые вопросы про экран можно безопасно отправлять сразу в vision.
    from .screen_fast_action import install_screen_fast_action
    install_screen_fast_action(JarvisApp)

    from .screen_cli import install_screen_cli
    install_screen_cli(JarvisApp)

    JarvisApp().run()


if __name__ == "__main__":
    main()
