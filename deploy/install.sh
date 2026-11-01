#!/usr/bin/env bash
# Установка на Linux-сервер (Ubuntu/Debian). Запускать из корня проекта: bash deploy/install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
cd "$DIR"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

[ -f .env ] || { cp .env.example .env; echo "Создан .env — заполните его при необходимости."; }

for svc in btc-monitor btc-dashboard; do
  sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" "deploy/$svc.service" \
    | sudo tee "/etc/systemd/system/$svc.service" > /dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now btc-monitor btc-dashboard
sudo systemctl --no-pager status btc-monitor btc-dashboard | head -n 20
echo "Готово. Логи: journalctl -u btc-monitor -f"
