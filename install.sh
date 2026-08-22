#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="$HOME/.local/share/stas-jarvis"
CONFIG_DIR="$HOME/.config/stas-jarvis"

echo "[1/8] Системные зависимости"
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip python3-dev build-essential \
  curl unzip ffmpeg \
  libportaudio2 portaudio19-dev libasound2-dev alsa-utils \
  espeak-ng playerctl brightnessctl xdg-utils wmctrl xdotool \
  gnome-screenshot \
  libgtk-3-0 libnss3 libxss1 libasound2

# Wayland computer-use backend. Optional because some older Ubuntu releases
# don't ship ydotool in their configured repositories.
if apt-cache show ydotool >/dev/null 2>&1; then
  sudo apt install -y ydotool || true
  if systemctl list-unit-files 2>/dev/null | grep -q '^ydotoold'; then
    sudo systemctl enable --now ydotoold || true
  fi
fi

echo "[2/8] Python venv"
cd "$ROOT"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
pip install -e .

echo "[3/8] Chromium для управляемого браузера"
python -m playwright install chromium

echo "[4/8] Русская Vosk-модель для wake-word"
mkdir -p "$DATA/models/vosk"
if [ ! -d "$DATA/models/vosk/vosk-model-small-ru-0.22" ]; then
  curl -L \
    "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip" \
    -o /tmp/vosk-model-small-ru-0.22.zip
  unzip -q -o /tmp/vosk-model-small-ru-0.22.zip -d "$DATA/models/vosk"
fi

echo "[5/8] Русский Piper voice"
mkdir -p "$DATA/models/piper"
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium"
if [ ! -f "$DATA/models/piper/ru_RU-ruslan-medium.onnx" ]; then
  curl -L "$PIPER_BASE/ru_RU-ruslan-medium.onnx" \
    -o "$DATA/models/piper/ru_RU-ruslan-medium.onnx"
fi
if [ ! -f "$DATA/models/piper/ru_RU-ruslan-medium.onnx.json" ]; then
  curl -L "$PIPER_BASE/ru_RU-ruslan-medium.onnx.json" \
    -o "$DATA/models/piper/ru_RU-ruslan-medium.onnx.json"
fi

echo "[6/8] Whisper Small (скачиваем заранее)"
mkdir -p "$DATA/models/whisper"
python - <<'PY'
from faster_whisper import WhisperModel
from pathlib import Path
root = Path.home() / ".local/share/stas-jarvis/models/whisper"
root.mkdir(parents=True, exist_ok=True)
print("Скачиваю/проверяю faster-whisper small...")
WhisperModel("small", device="cpu", compute_type="int8", download_root=str(root))
print("Whisper small готов.")
PY

echo "[7/8] Конфиг"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
  cp "$ROOT/config.example.json" "$CONFIG_DIR/config.json"
fi
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
fi

echo "[8/8] Готово"
echo
echo "OpenRouter: nano $ROOT/.env"
echo "Конфиг:    nano $CONFIG_DIR/config.json"
echo "Запуск:    $ROOT/run.sh"
echo
echo "Для LM Studio сначала запусти его Local Server на 127.0.0.1:1234."
echo "Wayland computer-use: проверить ydotool: command -v ydotool && systemctl status ydotoold"
