from __future__ import annotations

from .logging_setup import setup_logging


def main():
    setup_logging()

    # Жёсткий wake-word guard: никаких partial_ratio по любому куску речи.
    # Требуем целое слово + несколько подтверждений подряд.
    from .wake_guard import install_strict_wake_word
    install_strict_wake_word()

    # Расширяем базовый ToolRegistry безопасными системными настройками и
    # packaging-aware запуском приложений без выдачи root всему агенту.
    from .system_tools import install_system_tools
    install_system_tools()

    # Нормальный фокус уже открытых приложений без LLM-археологии через
    # xdotool/grep на несколько раундов.
    from .window_tools import install_window_tools
    install_window_tools()

    # On-demand screenshot + отдельный vision-вызов. Скриншот не делается
    # постоянно, только когда агенту реально нужно увидеть экран.
    from .screen_tools import install_screen_tools
    install_screen_tools()

    from .tui import JarvisApp

    # Очевидные desktop-команды выполняются локально, без LLM.
    from .fast_actions import install_fast_actions
    install_fast_actions(JarvisApp)

    # Прямые вопросы про экран идут сразу в vision-модель, не прогоняя
    # сначала общий agent loop.
    from .screen_fast_action import install_screen_fast_action
    install_screen_fast_action(JarvisApp)

    # Диагностическая команда /screen позволяет проверить screenshot+vision
    # вообще без основной LLM и без зависимости от формулировки запроса.
    from .screen_cli import install_screen_cli
    install_screen_cli(JarvisApp)

    JarvisApp().run()


if __name__ == "__main__":
    main()
