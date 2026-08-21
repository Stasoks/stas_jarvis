from __future__ import annotations

from .logging_setup import setup_logging


def main():
    setup_logging()

    # Жёсткий wake-word guard: никаких partial_ratio по любому куску речи.
    from .wake_guard import install_strict_wake_word
    install_strict_wake_word()

    # Обычные desktop/system tools.
    from .system_tools import install_system_tools
    install_system_tools()

    # Умный фокус уже открытых приложений.
    from .window_tools import install_window_tools
    install_window_tools()

    # Фоновый web_search/web_fetch через HTTP. Никаких внезапных окон Chromium
    # при обычном исследовании или поиске информации.
    from .web_tools import install_web_tools
    install_web_tools()

    # On-demand screenshot + отдельный vision-вызов.
    from .screen_tools import install_screen_tools
    install_screen_tools()

    from .tui import JarvisApp

    # Очевидные desktop-команды выполняются локально, без LLM.
    from .fast_actions import install_fast_actions
    install_fast_actions(JarvisApp)

    # Прямые вопросы про экран идут сразу в vision-модель.
    from .screen_fast_action import install_screen_fast_action
    install_screen_fast_action(JarvisApp)

    from .screen_cli import install_screen_cli
    install_screen_cli(JarvisApp)

    JarvisApp().run()


if __name__ == "__main__":
    main()
