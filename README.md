# STAS JARVIS

Личный voice-first desktop-агент для Linux: постоянно ждёт wake-word «Джарвис», распознаёт команду локально и отдаёт её LLM, которая может управлять компьютером через структурированные tools.

## Возможности

- wake-word через лёгкий русский Vosk;
- команда через **faster-whisper Small** на CPU/int8;
- Whisper загружается в фоне сразу после старта приложения;
- один непрерывный audio stream для wake-word и команды, чтобы не обрезать начало фразы;
- LM Studio и OpenRouter через OpenAI-compatible API;
- автоматический fallback OpenRouter → LM Studio при ошибке провайдера;
- русский Piper TTS с мгновенной остановкой через `Esc` или `/stop`;
- запуск Linux-приложений и открытие папок;
- управление музыкой через `playerctl`;
- громкость через `wpctl`/`pactl`;
- яркость через `brightnessctl`;
- файлы и shell-команды;
- управляемый Chromium через Playwright;
- Textual TUI с историей диалога, логами, переключением модели и микрофона;
- локальные ответы на время/дату без обращения к LLM.

## Установка на Ubuntu

```bash
git clone https://github.com/Stasoks/stas_jarvis.git
cd stas_jarvis
chmod +x install.sh run.sh
./install.sh
```

Установщик поставит системные зависимости, создаст `.venv`, скачает русский Vosk, Piper voice, Whisper Small и Chromium для Playwright.

## OpenRouter

После установки:

```bash
nano .env
```

Вставь ключ:

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

Ключ не коммитится: `.env` находится в `.gitignore`.

По умолчанию в `config.example.json` используется:

```text
poolside/laguna-s-2.1:free
```

Модель можно поменять прямо из TUI.

## LM Studio

Запусти Local Server в LM Studio на стандартном адресе:

```text
http://127.0.0.1:1234/v1
```

В JARVIS:

```text
/provider lmstudio
/models qwen
/model ТОЧНЫЙ_ID_МОДЕЛИ
```

Вернуться на OpenRouter:

```text
/provider openrouter
```

## Запуск

```bash
./run.sh
```

После запуска Whisper Small начинает прогружаться в память в фоне. Wake listener при этом стартует отдельно.

Голосовой сценарий:

```text
«Джарвис»
     ↓
Vosk обнаруживает wake-word
     ↓
тот же микрофонный stream продолжает писать команду
     ↓
Whisper Small
     ↓
LLM + tools
     ↓
действие на компьютере
     ↓
Piper TTS
```

Примеры:

> Джарвис, открой Telegram.

> Джарвис, поставь яркость на 40 процентов.

> Джарвис, следующий трек.

> Джарвис, открой папку с проектами.

> Джарвис, найди в интернете документацию llama.cpp и расскажи главное.

## Команды TUI

```text
/help
/status

/provider lmstudio
/provider openrouter
/model <model-id>
/models [фильтр]

/voice on|off
/tts on|off
/stop

/devices
/mic <id|default>

/logs [N]
/tools
/clear
/quit
```

Во время озвучки нажми **Esc**, чтобы остановить только речь, не закрывая JARVIS.

## Конфиг

Пользовательский конфиг создаётся здесь:

```text
~/.config/stas-jarvis/config.json
```

Шаблон находится в `config.example.json`.

Ключевые параметры распознавания:

```json
{
  "voice": {
    "whisper_model": "small",
    "whisper_compute_type": "int8",
    "pre_roll_ms": 900,
    "post_speech_silence_ms": 1100,
    "vad_aggressiveness": 1,
    "min_rms_threshold": 120
  }
}
```

Если используется не тот микрофон:

```text
/devices
/mic 3
```

Вернуть системный default:

```text
/mic default
```

## Tools

Сейчас агент получает, среди прочего:

```text
get_current_datetime
open_application
open_folder
open_url
media_control
set_volume
set_brightness
focus_window
type_text
press_keys
list_files
read_file
write_file
search_files
run_shell
browser_open
browser_search
browser_read
browser_list_elements
browser_click
browser_fill
browser_back
```

## X11 и Wayland

Работа с файлами, приложениями, `playerctl`, `brightnessctl` и Playwright не зависит от `xdotool`.

`type_text`, `press_keys` и часть управления окнами заметно надёжнее работают в X11. Для полного desktop-control на Ubuntu можно выбрать **Ubuntu on Xorg** на экране входа.

## Безопасность

`run_shell` не является полноценной песочницей. Встроенный фильтр блокирует очевидные разрушительные команды вроде `sudo`, `mkfs`, reboot/shutdown и `rm -rf /`, но LLM всё равно получает возможность запускать команды от текущего пользователя.

Shell можно полностью отключить в конфиге:

```json
{
  "tools": {
    "allow_shell": false
  }
}
```

API-ключи, `.venv`, модели и runtime-файлы не должны попадать в Git благодаря `.gitignore`.
