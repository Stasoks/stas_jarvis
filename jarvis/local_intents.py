from __future__ import annotations

import re
from datetime import datetime

_TIME_PATTERNS = (
    r"\bкоторый\s+час\b",
    r"\bсколько\s+(?:сейчас\s+)?времени\b",
    r"\bвремя\s+(?:сейчас|у\s+нас)\b",
)
_DATE_PATTERNS = (
    r"\bкакое\s+сегодня\s+число\b",
    r"\bкакая\s+сегодня\s+дата\b",
    r"\bкакой\s+сегодня\s+день\b",
)

_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

def try_local_intent(text: str) -> str | None:
    """Очень маленький offline fast-path для вещей, которые смешно отправлять в облако."""
    s = text.casefold().strip()
    now = datetime.now().astimezone()

    if any(re.search(p, s) for p in _TIME_PATTERNS):
        return f"Сейчас {now:%H:%M}."

    if any(re.search(p, s) for p in _DATE_PATTERNS):
        return f"Сегодня {now.day} {_MONTHS[now.month]} {now.year} года."

    return None
