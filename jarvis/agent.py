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
13. Не занимайся диагностикой «на всякий случай». Если специализированный tool сработал — остановись.
14. Если tool вернул ошибку, сделай максимум одну разумную диагностическую попытку или альтернативу.
    Не запускай длинные цепочки shell-команд без необходимости.
"""


class Agent:
    def __init__(self, llm, tools, config: dict):
        self.llm = llm
        self.tools = tools
        self.config = config

        # В долгосрочную историю попадают ТОЛЬКО пользовательские сообщения
        # и финальные ответы. Tool calls/results храним в jsonl для отладки,
        # но никогда не тащим их в следующий пользовательский запрос.
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

    def _history_for_request(self) -> list[dict[str, Any]]:
        """Ограниченная разговорная история без tool-мусора."""
        agent_cfg = self.config.get("agent", {})
        max_messages = min(int(agent_cfg.get("history_messages", 8)), 10)
        max_chars = int(agent_cfg.get("history_chars", 12000))

        selected: list[dict[str, Any]] = []
        used = 0

        for msg in reversed(self.history[-max_messages:]):
            if msg.get("role") not in ("user", "assistant"):
                continue

            content = str(msg.get("content") or "")
            if len(content) > 4000:
                content = content[:4000] + "\n...[старое сообщение обрезано]"

            if selected and used + len(content) > max_chars:
                break

            selected.append({"role": msg["role"], "content": content})
            used += len(content)

        selected.reverse()
        return selected

    def _compact_tool_result(self, result: str) -> str:
        """Не позволяем ls/cat/browser dump раздувать каждый следующий LLM round."""
        max_chars = int(self.config.get("agent", {}).get("tool_result_chars", 4000))
        if len(result) <= max_chars:
            return result
        return result[:max_chars] + "\n...[tool output truncated]"

    def ask(self, text: str, on_tool=None) -> str:
        on_tool = on_tool or (lambda name, args, result: None)

        user_msg = {"role": "user", "content": text}
        self.history.append(user_msg)
        self._persist(user_msg)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history_for_request())

        schemas = self.tools.schemas()

        # Старые config.json могли содержать 8. Жёсткий потолок 4 не даёт
        # модели сжечь десятки запросов на одной команде.
        configured_rounds = int(self.config.get("agent", {}).get("max_tool_rounds", 4))
        max_rounds = max(1, min(configured_rounds, 4))

        for round_idx in range(max_rounds):
            msg = self.llm.chat(messages, tools=schemas)
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            if not tool_calls:
                assistant = {
                    "role": "assistant",
                    "content": content.strip() or "Готово.",
                }
                self.history.append(assistant)
                self._persist(assistant)
                return assistant["content"]

            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }

            # Нужен только внутри ТЕКУЩЕГО tool-loop.
            messages.append(assistant_msg)
            self._persist({
                **assistant_msg,
                "_internal": "tool_loop",
                "_round": round_idx + 1,
            })

            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "")
                raw_args = (tc.get("function") or {}).get("arguments", "{}")

                try:
                    args = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else (raw_args or {})
                    )
                except json.JSONDecodeError:
                    args = {}

                result = self.tools.execute(fn, args)
                on_tool(fn, args, result)

                compact_result = self._compact_tool_result(result)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn,
                    "content": compact_result,
                }

                # Тоже только текущий запрос.
                messages.append(tool_msg)
                self._persist({
                    **tool_msg,
                    "_internal": "tool_loop",
                    "_full_result_chars": len(result),
                })

        final = (
            "Я остановил выполнение после четырёх шагов, чтобы не зациклиться "
            "и не сжигать токены. Посмотри `/logs`, если действие не завершилось."
        )
        assistant = {"role": "assistant", "content": final}
        self.history.append(assistant)
        self._persist(assistant)
        return final
