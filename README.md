# STAS JARVIS

Личный Linux-агент без чужой магии из пяти daemon'ов.

## Что умеет

- постоянно слушает wake-word `Джарвис` через лёгкий Vosk;
- после wake-word записывает команду и распознаёт **Whisper Small**;
- подключается к:
  - **LM Studio**: `http://127.0.0.1:1234/v1`;
  - **OpenRouter**: `https://openrouter.ai/api/v1`;
- native OpenAI tool calling;
- открывает приложения;
- открывает URL;
- управляет музыкой через `playerctl`;
- громкость через `wpctl`/`pactl`;
- яркость через `brightnessctl`;
- окна/клавиатура через `wmctrl`/`xdotool`;
- файлы;
- shell;
- отдельный управляемый Chromium через Playwright;
- TUI с историей диалога, `/logs`, `/model`, `/provider`, `/voice`, `/tts`.

## Установка

```bash
cd ~/Downloads
unzip stas_jarvis.zip
cd stas_jarvis
chmod +x install.sh run.sh
./install.sh
```

### OpenRouter

```bash
nano .env
```

Вставить:

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

По умолчанию в `config.json` указан:

```text
poolside/laguna-s-2.1:free
```

### LM Studio

Запусти Local Server в LM Studio, затем внутри JARVIS:

```text
/provider lmstudio
/models qwen
/model ТОЧНЫЙ_ID_МОДЕЛИ
```

## Запуск

```bash
./run.sh
```

Потом можно говорить:

> Джарвис

После короткого сигнала:

> Открой Telegram

или:

> Включи следующий трек

или:

> Поставь яркость 40 процентов

или:

> Открой браузер, найди документацию llama.cpp по speculative decoding и расскажи главное.

## Команды TUI

```text
/help
/status
/provider lmstudio
/provider openrouter
/model <id>
/models [фильтр]
/voice on|off
/tts on|off
/logs 100
/tools
/clear
/quit
```

## Конфиг

```text
~/.config/stas-jarvis/config.json
```

Основные настройки:

```json
{
  "voice": {
    "whisper_model": "small"
  }
}
```

Установщик заранее скачивает **Whisper Small**, а само приложение начинает загружать его в память в фоне сразу после старта. Поэтому первый голосовой запрос не должен внезапно ждать инициализацию модели.

## Wayland

`open_application`, `brightnessctl`, `playerctl`, файлы и Playwright работают нормально.

`xdotool` и часть управления окнами рассчитаны в первую очередь на X11.
Если тебе критичны `type_text`, `press_keys`, `focus_window`, проще зайти в сессию **Ubuntu on Xorg**.

## Безопасность

`run_shell` включён. Он блокирует `sudo`, reboot/shutdown, mkfs, очевидный `rm -rf /` и несколько других разрушительных паттернов, но это **не sandbox**.

Можно выключить:

```json
"tools": {
  "allow_shell": false
}
```

Тогда остаются структурированные инструменты управления компьютером.


## Улучшенное распознавание (v0.2)

Теперь запись команды использует WebRTC VAD вместо одного RMS-порога, держит
600 мс аудио до начала речи, не теряет первое слово сразу после beep и
нормализует тихую запись перед Whisper.

Если распознавание всё ещё странное, сначала проверь, какой микрофон выбран:

```text
/devices
```

Затем:

```text
/mic 3
```

Где `3` — id реального встроенного/USB микрофона.

Вернуться к системному default:

```text
/mic default
```

### Время работает даже без интернета

`который час?`, `сколько сейчас времени?`, `какое сегодня число?`
обрабатываются локально и не тратят запрос OpenRouter.

В tool-loop также есть `get_current_datetime`, поэтому более сложные вопросы
про текущие дату/время модель не должна выдумывать.

### OpenRouter 429

Если OpenRouter отвечает HTTP 429, Jarvis автоматически попробует
`fallback_provider` из конфига. По умолчанию это `lmstudio`.

Для этого LM Studio Local Server должен быть запущен. Если не нужен fallback:

```json
"fallback_provider": null
```


## v0.3 — починка обрезанных команд

Главное изменение: после wake-word микрофон **больше не закрывается и не
переоткрывается**. Wake-word и команда читаются из одного непрерывного
`RawInputStream`.

Это исправляет типичный баг:

```text
"Джарвис, открой папку с проектами"
                 ↓
старый вариант: "папку с проектами"
```

Теперь используется:
- один непрерывный stream;
- ~900 мс pre-roll;
- WebRTC VAD;
- 1.1 с тишины для завершения фразы;
- мягкая нормализация записи;
- русский initial prompt + hotwords;
- Whisper `small`;
- Whisper начинает загружаться в фоне **сразу при старте приложения**.

То есть первый голосовой запрос больше не должен внезапно запускать загрузку
Whisper.


## v0.4 — мгновенная остановка озвучки

Во время речи JARVIS:

- нажми `Esc` — текущая озвучка немедленно остановится;
- либо введи `/stop`.

Сам JARVIS продолжит работать, история и voice listener не закрываются.
`Ctrl+C` по-прежнему завершает приложение целиком.
