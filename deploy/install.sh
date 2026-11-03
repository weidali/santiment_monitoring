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

# Веб-экран по умолчанию слушает только localhost (доступ через SSH-туннель).
# Чтобы открыть его в локальной сети, запустите: WEB_HOST=0.0.0.0 bash deploy/install.sh
WEB_HOST="${WEB_HOST:-127.0.0.1}"

# Старый дашборд на Streamlit больше не используется
if systemctl list-unit-files | grep -q '^btc-dashboard.service'; then
  sudo systemctl disable --now btc-dashboard || true
  sudo rm -f /etc/systemd/system/btc-dashboard.service
fi

for svc in btc-monitor btc-web; do
  sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" -e "s|__HOST__|$WEB_HOST|g" "deploy/$svc.service" \
    | sudo tee "/etc/systemd/system/$svc.service" > /dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now btc-monitor btc-web
sudo systemctl restart btc-monitor btc-web
sudo systemctl --no-pager status btc-monitor btc-web | head -n 20
echo "Готово. Логи: journalctl -u btc-monitor -f"
