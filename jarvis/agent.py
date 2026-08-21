from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SESSIONS_DIR

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты Джарвис, локальный desktop-агент пользователя на Linux.

Твоя задача не просто объяснять, а ВЫПОЛНЯТЬ просьбы через доступные tools.

Ключевые правила:
1. Для поиска информации, исследований, свежих новостей, моделей и бенчмарков используй web_search и web_fetch.
   Они работают ФОНОВО и не открывают никаких окон.
2. browser_open/browser_search/browser_read/browser_click/browser_fill и open_url используй ТОЛЬКО если пользователь
   явно попросил открыть, показать или интерактивно использовать сайт/браузер. Не открывай GUI-браузер ради обычного поиска.
3. Для исследования не останавливайся после первого результата. Собери несколько независимых источников,
   открой релевантные страницы через web_fetch, сравни данные и в финальном ответе укажи названия/URL источников.
4. Если пользователь спрашивает точное время/дату — вызови get_current_datetime и не угадывай.
5. Если пользователь просит открыть приложение, файл, включить/остановить музыку, изменить громкость/яркость
   или изменить файл — используй соответствующий специализированный tool.
6. Не говори «я не могу взаимодействовать с компьютером», если для действия есть tool.
7. Не утверждай, что действие выполнено, пока tool не вернул успех.
8. Для системных настроек используй system_control. Не говори «нет прав», пока не попробовал подходящий tool.
9. Для переключения между уже открытыми программами используй focus_application, а не run_shell/xdotool вручную.
10. Для обычных действий предпочитай специализированные tools, а не run_shell.
11. run_shell используй только когда специализированного инструмента недостаточно. Не используй sudo.
12. Отвечай по-русски, если пользователь не попросил другой язык.
13. После успешного простого действия отвечай коротко. Для исследования, наоборот, дай содержательный итог.
14. Не занимайся диагностикой «на всякий случай». Если специализированный tool сработал — остановись.
15. Если один и тот же tool с теми же аргументами уже дважды не помог, измени подход, а не повторяй его бесконечно.
"""

_RESEARCH_RE = re.compile(
    r"(?:исслед|сравн|обзор|новост|свеж|недавно|последн|актуальн|релиз|выш(?:ел|ла|ли|едш)|"
    r"бенчмарк|benchmark|модел|рынок|источник|поищи\s+информац|найди\s+информац|что\s+нового)",
    re.IGNORECASE,
)

_GUI_RE = re.compile(
    r"(?:открой|покажи|запусти|выведи|перейди).{0,40}(?:браузер|сайт|страниц|youtube|ютуб|url|ссылк)|"
    r"(?:браузер|сайт|youtube|ютуб).{0,30}(?:открой|покажи)",
    re.IGNORECASE,
)

_GUI_TOOLS = {
    "open_url",
    "browser_open",
    "browser_search",
    "browser_read",
    "browser_list_elements",
    "browser_click",
    "browser_fill",
    "browser_back",
}

_RESEARCH_TOOLS = {
    "web_search",
    "web_fetch",
    "get_current_datetime",
    "read_file",
    "write_file",
    "list_files",
    "search_files",
}


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

    def _history_for_request(self) -> list[dict[str, Any]]:
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
        max_chars = int(self.config.get("agent", {}).get("tool_result_chars", 5000))
        if len(result) <= max_chars:
            return result
        return result[:max_chars] + "\n...[tool output truncated]"

    @staticmethod
    def _is_research(text: str) -> bool:
        return bool(_RESEARCH_RE.search(text))

    @staticmethod
    def _wants_gui(text: str) -> bool:
        return bool(_GUI_RE.search(text))

    def _schemas_for_request(self, text: str, research: bool) -> list[dict[str, Any]]:
        schemas = self.tools.schemas()
        wants_gui = self._wants_gui(text)

        # Если пользователь не попросил ВИДИМО открыть страницу, GUI-tools для
        # модели вообще не существуют. Значит случайно выпрыгнуть Chromium не может.
        if not wants_gui:
            schemas = [
                s for s in schemas
                if (s.get("function") or {}).get("name") not in _GUI_TOOLS
            ]

        # Для исследования даём маленький toolset: поиск, чтение источников и
        # опционально работу с файлами. Это заметно дешевле полного ящика tools.
        if research and not wants_gui:
            schemas = [
                s for s in schemas
                if (s.get("function") or {}).get("name") in _RESEARCH_TOOLS
            ]

        return schemas

    def _budgets(self, research: bool) -> tuple[int, int]:
        cfg = self.config.get("agent", {})
        if research:
            rounds = max(10, int(cfg.get("research_tool_rounds", 12)))
            calls = max(20, int(cfg.get("research_max_tool_calls", 24)))
            return min(rounds, 16), min(calls, 36)

        # Старый max_tool_rounds=4 больше не душит обычные многошаговые задачи.
        rounds = max(6, int(cfg.get("max_tool_rounds", 8)))
        calls = max(12, int(cfg.get("max_tool_calls", 18)))
        return min(rounds, 12), min(calls, 24)

    def _finalize_without_tools(self, messages: list[dict[str, Any]], reason: str) -> str:
        final_messages = list(messages)
        final_messages.append({
            "role": "system",
            "content": (
                "Бюджет инструментов на этот запрос исчерпан. Больше tools вызывать нельзя. "
                "Сейчас обязательно сформируй лучший финальный ответ из уже собранных данных. "
                "Не жалуйся на лимит и не обещай продолжить позже. "
                f"Причина завершения tool-loop: {reason}."
            ),
        })
        try:
            msg = self.llm.chat(final_messages, tools=None)
            content = str(msg.get("content") or "").strip()
            if content:
                return content
        except Exception:
            log.exception("Final synthesis after tool budget failed")
        return "Не удалось завершить запрос после исчерпания бюджета инструментов. Подробности есть в /logs."

    def ask(self, text: str, on_tool=None) -> str:
        on_tool = on_tool or (lambda name, args, result: None)

        user_msg = {"role": "user", "content": text}
        self.history.append(user_msg)
        self._persist(user_msg)

        research = self._is_research(text)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history_for_request())
        schemas = self._schemas_for_request(text, research)
        max_rounds, max_tool_calls = self._budgets(research)

        log.info(
            "AGENT budget research=%s rounds=%d max_tool_calls=%d schemas=%d gui=%s",
            research,
            max_rounds,
            max_tool_calls,
            len(schemas),
            self._wants_gui(text),
        )

        signature_counts: Counter[str] = Counter()
        total_tool_calls = 0
        stop_reason = "round limit"

        for round_idx in range(max_rounds):
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
            self._persist({
                **assistant_msg,
                "_internal": "tool_loop",
                "_round": round_idx + 1,
            })

            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "")
                raw_args = (tc.get("function") or {}).get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}

                signature = fn + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)
                signature_counts[signature] += 1
                total_tool_calls += 1

                if signature_counts[signature] > 2:
                    result = json.dumps({
                        "ok": False,
                        "message": (
                            "Этот же tool с теми же аргументами уже вызывался дважды. "
                            "Не повторяй его; измени запрос или подход."
                        ),
                    }, ensure_ascii=False)
                else:
                    result = self.tools.execute(fn, args)
                    on_tool(fn, args, result)

                compact_result = self._compact_tool_result(result)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn,
                    "content": compact_result,
                }
                messages.append(tool_msg)
                self._persist({
                    **tool_msg,
                    "_internal": "tool_loop",
                    "_full_result_chars": len(result),
                })

                if total_tool_calls >= max_tool_calls:
                    stop_reason = f"tool call limit {max_tool_calls}"
                    break

            if total_tool_calls >= max_tool_calls:
                break

        final = self._finalize_without_tools(messages, stop_reason)
        assistant = {"role": "assistant", "content": final}
        self.history.append(assistant)
        self._persist(assistant)
        return final
