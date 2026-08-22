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

SYSTEM_PROMPT = """Ты Джарвис, полноценный локальный desktop/browser-агент пользователя на Linux.
Твоя задача не объяснять, как пользователь мог бы сделать действие, а самостоятельно доводить задачу до результата через tools.

ОСНОВНЫЕ ПРАВИЛА
1. Для обычного поиска информации, research, новостей, моделей и бенчмарков используй web_search + web_fetch. Они работают фоном без GUI.
2. Если пользователь явно просит открыть/показать сайт, включить веб-сервис, найти что-то В БРАУЗЕРЕ, скачать через браузер,
   нажать кнопку, поменять настройку через GUI или вообще выполнить многошаговое действие на экране — это computer-use задача.
3. В browser-задачах предпочитай DOM/Playwright: browser_open/browser_search -> browser_observe -> browser_click/browser_fill/... -> browser_observe.
   browser_observe уже возвращает URL, текст и стабильные refs. После существенного перехода или клика наблюдай страницу снова.
4. Не считай задачу выполненной после одного открытия страницы. Продолжай, пока реально не достигнута цель.
   Пример: «включи Яндекс Музыку» означает открыть/найти сервис, дождаться страницы, найти нужный play/станцию/трек и довести до начала воспроизведения.
5. Если DOM браузера недостаточен (canvas, native dialog, системные настройки, произвольное приложение), используй computer_observe.
   Он возвращает элементы и координаты 0..1000. Затем computer_click/computer_type/computer_key/computer_scroll/computer_wait.
   После изменения GUI снова вызови computer_observe. Никогда не угадывай координаты без свежего наблюдения.
6. Для системных настроек сначала пробуй system_control. Если нужной настройки там нет, переходи к полноценному GUI через computer_observe.
7. Для уже открытых приложений используй focus_application, но если он не нашёл окно — не сдавайся: computer_observe + computer_click может найти его визуально.
8. Для research не открывай GUI-браузер без явной просьбы пользователя. Собери несколько источников, читай страницы через web_fetch, затем синтезируй ответ.
9. browser_download используй только после browser_observe и только когда актуальный ref действительно является нужной ссылкой/кнопкой скачивания.
10. Если сайт требует пароль, 2FA, CAPTCHA, платёж или подтверждение покупки — остановись непосредственно перед этим шагом и попроси пользователя вмешаться.
    Не вводи пароли/секреты самостоятельно. Не подтверждай покупку и не отправляй сообщения без явного запроса пользователя.
11. Если специализированный tool сработал, не запускай shell-диагностику «на всякий случай». run_shell — последний резерв.
12. Не используй sudo. Не утверждай, что действие выполнено, пока интерфейс/tool не подтвердил результат.
13. Если один и тот же tool с теми же аргументами дважды не помог, измени подход.
14. Для точного времени/даты используй get_current_datetime.
15. Отвечай по-русски, если пользователь не попросил другой язык. После desktop-действия отвечай коротко, после research — содержательно.
"""

_RESEARCH_RE = re.compile(
    r"(?:исслед|сравн|обзор|новост|свеж|недавно|последн|актуальн|релиз|выш(?:ел|ла|ли|едш)|"
    r"бенчмарк|benchmark|модел|рынок|источник|поищи\s+информац|найди\s+информац|что\s+нового)",
    re.IGNORECASE,
)

_GUI_RE = re.compile(
    r"(?:открой|покажи|запусти|перейди|переключ|нажми|кликни|включи|выключи|скачай|скачать|"
    r"выбери|заполни|найди|измени|поставь).{0,90}"
    r"(?:браузер|сайт|страниц|youtube|ютуб|яндекс\s*музык|spotify|спотифай|url|ссылк|скачив|"
    r"настройк|кнопк|вкладк|окн|меню|приложен)|"
    r"(?:в\s+браузер|через\s+браузер|на\s+экране|в\s+настройк|по\s+интерфейс)",
    re.IGNORECASE,
)

_GUI_TOOLS = {
    "open_url",
    "browser_open", "browser_search", "browser_read", "browser_list_elements",
    "browser_click", "browser_fill", "browser_back", "browser_observe",
    "browser_press", "browser_scroll", "browser_wait", "browser_tabs",
    "browser_select_tab", "browser_download",
    "computer_observe", "computer_click", "computer_type", "computer_key",
    "computer_scroll", "computer_wait",
}

_GUI_ALLOWED_TOOLS = _GUI_TOOLS | {
    "open_application", "open_folder", "focus_application", "system_control",
    "see_screen", "media_control", "set_volume", "set_brightness",
    "get_current_datetime", "list_files", "read_file", "write_file", "search_files",
    "web_search", "web_fetch",
}

_RESEARCH_TOOLS = {
    "web_search", "web_fetch", "get_current_datetime",
    "read_file", "write_file", "list_files", "search_files",
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

    def _schemas_for_request(self, text: str, research: bool, wants_gui: bool) -> list[dict[str, Any]]:
        schemas = self.tools.schemas()

        if wants_gui:
            # GUI request gets a focused computer-use toolbox rather than every
            # filesystem/shell toy in the garage. Cheaper prompt, better choices.
            schemas = [
                s for s in schemas
                if (s.get("function") or {}).get("name") in _GUI_ALLOWED_TOOLS
            ]
            return schemas

        # Without explicit GUI intent the model cannot accidentally spawn a browser/window.
        schemas = [
            s for s in schemas
            if (s.get("function") or {}).get("name") not in _GUI_TOOLS
        ]

        if research:
            schemas = [
                s for s in schemas
                if (s.get("function") or {}).get("name") in _RESEARCH_TOOLS
            ]
        return schemas

    def _budgets(self, research: bool, wants_gui: bool) -> tuple[int, int]:
        cfg = self.config.get("agent", {})

        if wants_gui:
            rounds = max(16, int(cfg.get("gui_tool_rounds", 20)))
            calls = max(32, int(cfg.get("gui_max_tool_calls", 44)))
            return min(rounds, 24), min(calls, 60)

        if research:
            rounds = max(10, int(cfg.get("research_tool_rounds", 12)))
            calls = max(20, int(cfg.get("research_max_tool_calls", 24)))
            return min(rounds, 16), min(calls, 36)

        rounds = max(6, int(cfg.get("max_tool_rounds", 8)))
        calls = max(12, int(cfg.get("max_tool_calls", 18)))
        return min(rounds, 12), min(calls, 24)

    def _finalize_without_tools(self, messages: list[dict[str, Any]], reason: str) -> str:
        final_messages = list(messages)
        final_messages.append({
            "role": "system",
            "content": (
                "Бюджет инструментов на этот запрос исчерпан. Больше tools вызывать нельзя. "
                "Сформируй лучший финальный ответ из уже собранных данных. "
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
        return "Не удалось завершить запрос. Подробности есть в /logs."

    def ask(self, text: str, on_tool=None) -> str:
        on_tool = on_tool or (lambda name, args, result: None)
        user_msg = {"role": "user", "content": text}
        self.history.append(user_msg)
        self._persist(user_msg)

        research = self._is_research(text)
        wants_gui = self._wants_gui(text)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history_for_request())
        schemas = self._schemas_for_request(text, research, wants_gui)
        max_rounds, max_tool_calls = self._budgets(research, wants_gui)

        log.info(
            "AGENT budget research=%s gui=%s rounds=%d max_tool_calls=%d schemas=%d",
            research, wants_gui, max_rounds, max_tool_calls, len(schemas),
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

            assistant_msg = {"role": "assistant", "content": content, "tool_calls": tool_calls}
            messages.append(assistant_msg)
            self._persist({**assistant_msg, "_internal": "tool_loop", "_round": round_idx + 1})

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
                        "message": "Этот же tool с теми же аргументами уже вызывался дважды. Измени подход.",
                    }, ensure_ascii=False)
                else:
                    result = self.tools.execute(fn, args)
                    on_tool(fn, args, result)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": fn,
                    "content": self._compact_tool_result(result),
                }
                messages.append(tool_msg)
                self._persist({**tool_msg, "_internal": "tool_loop", "_full_result_chars": len(result)})

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
