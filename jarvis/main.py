from __future__ import annotations

from .logging_setup import setup_logging


def main():
    setup_logging()

    # Расширяем базовый ToolRegistry безопасными системными настройками
    # (Wi-Fi, Bluetooth, night light, power profile и т.д.) без выдачи root
    # всему агенту.
    from .system_tools import install_system_tools
    install_system_tools()

    from .tui import JarvisApp
    JarvisApp().run()


if __name__ == "__main__":
    main()
