from __future__ import annotations
from .logging_setup import setup_logging

def main():
    setup_logging()
    from .tui import JarvisApp
    JarvisApp().run()

if __name__ == "__main__":
    main()
