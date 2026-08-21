from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SESSIONS_DIR

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты Джарвис, локальный desktop-агент пользователя на Linux.

Твоя задача не просто объяснять, а ВЫПОЛНЯТЬ просьбы через доступные tools.

Правила:
1. Если пользователь спрашивает точное время/дату — вызови get_current_datetime и не угадывай.
2. Если пользователь просит открыть приложение, сайт, файл, включить/остановить музыку,
   изменить громкость/яркость, что-то найти в браузере или изменить файл — используй tool.
3. Не говори «я не могу взаимодействовать с компьютером», если для действия есть tool.
4. Не утверждай, что действие выполнено, пока tool не вернул успех.
5. Для браузера:
   - browser_open/browser_search открывают страницу;
   - browser_read читает страницу;
   - перед кликом/вводом вызови browser_list_elements и используй ref e1/e2/...
6. Для обычных системных настроек используй system_control. Wi-Fi, Bluetooth, ночной свет,
   тёмная тема, режим питания, уведомления, таймаут экрана и mute микрофона обычно НЕ требуют sudo
   в активной пользовательской сессии.
7. Не говори пользователю «нет прав» или «не могу менять настройки», пока не попробовал подходящий
   специализированный tool и не получил реальную ошибку от ОС.
8. Для обычных системных действий предпочитай специализированные tools, а не run_shell.
9. run_shell используй только когда специализированного инструмента недостаточно.
10. Не используй sudo и не пытайся обходить safety-фильтры.
11. Отвечай по-русски, если пользователь не попросил другой язык.
12. После успешного действия отвечай коротко. Не пересказывай внутренний tool-loop.
13. Если tool вернул ошибку, попробуй разумную альтернативу. Если не получилось — честно сообщи причину.
"""

class Agent:
    def __init__(self, llm, tools, config: dict):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.history: list[dict[str, Any]] = []
        self.session_path = self._new_session_path()

    def _new_session_path(self) -> Path:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".jsonl"
        return SESSIONS_DIR / name

    def _persist(self, message: dict[str, Any]) -> None:
        with self.session_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def clear(self) -> None:
        self.history.clear()
        self.session_path = self._new_session_path()

    def ask(self, text: str, on_tool=None) -> str:
        on_tool = on_tool or (lambda name, args, result: None)
        user_msg = {"role": "user", "content": text}
        self.history.append(user_msg)
        self._persist(user_msg)

        max_hist = int(self.config["agent"].get("history_messages", 24))
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-max_hist:]
        schemas = self.tools.schemas()
        max_rounds = int(self.config["agent"].get("max_tool_rounds", 8))

        for _ in range(max_rounds):
            msg = self.llm.chat(messages, tools=schemas)
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            if not tool_calls:
                assistant = {"role": "assistant", "content": content.strip() or "Готово."}
                self.history.append(assistant)
                self._persist(assistant)
                return assistant["content"]

            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)
            self.history.append(assistant_msg)
            self._persist(assistant_msg)

            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "")
                raw_args = (tc.get("function") or {}).get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}

                result = self.tools.execute(fn, args)
                on_tool(fn, args, result)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn,
                    "content": result,
                }
                messages.append(tool_msg)
                self.history.append(tool_msg)
                self._persist(tool_msg)

        final = "Я остановил tool-loop после максимального числа шагов. Посмотри `/logs`, если нужно понять, где он зациклился."
        assistant = {"role": "assistant", "content": final}
        self.history.append(assistant)
        self._persist(assistant)
        return final
