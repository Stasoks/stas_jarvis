#!/usr/bin/env bash
set -euo pipefail

SOCKET="/run/stas-jarvis-ydotool.sock"
UID_NOW="$(id -u)"
GID_NOW="$(id -g)"

sudo apt update
sudo apt install -y ydotool gnome-screenshot xdotool wmctrl

YDOTOOLD="$(command -v ydotoold || true)"
if [ -z "$YDOTOOLD" ]; then
  echo "ydotoold не найден после установки ydotool"
  exit 1
fi

sudo tee /etc/systemd/system/stas-jarvis-ydotoold.service >/dev/null <<EOF
[Unit]
Description=STAS JARVIS ydotool daemon
After=multi-user.target

[Service]
Type=simple
User=root
ExecStart=$YDOTOOLD --socket-path=$SOCKET --socket-own=$UID_NOW:$GID_NOW --socket-perm=0600
Restart=on-failure
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now stas-jarvis-ydotoold.service

sleep 1
export YDOTOOL_SOCKET="$SOCKET"

if ydotool key 42:1 42:0 >/dev/null 2>&1; then
  echo "computer-use backend готов: ydotool + $SOCKET"
else
  echo "ydotool установлен, но тест не прошёл."
  echo "Проверь: sudo systemctl status stas-jarvis-ydotoold.service"
  echo "И: ls -l $SOCKET"
  exit 2
fi
