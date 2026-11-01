"""Настройки монитора. Всё, что стоит подкрутить под себя, находится здесь."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env():
    """Читает .env рядом с кодом (KEY=value). Переменные окружения имеют приоритет."""
    env = BASE_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# Как часто собирать данные (секунды) в режиме непрерывной работы
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", 15 * 60))

DB_PATH = os.environ.get("BTC_MONITOR_DB", str(BASE_DIR / "btc_monitor.sqlite"))

# Telegram-уведомления (необязательно). Создайте бота через @BotFather.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Рынки Polymarket для отслеживания: slug события из адреса
# https://polymarket.com/event/<slug>. Например, контроль Палаты, Сената, CLARITY Act.
POLYMARKET_EVENT_SLUGS = [
    # "which-party-will-win-the-house-in-2026",
]
POLYMARKET_ALERT_PP = 10  # уведомлять, если вероятность сдвинулась на N п.п. за 24 часа

# Веса групп в итоговом индексе (сумма = 1)
GROUP_WEIGHTS = {
    "flows": 0.40,        # ETF, Coinbase Premium, стейблкоины
    "positioning": 0.20,  # плечо: OI, funding
    "trend": 0.15,        # цена против скользящих средних
    "macro": 0.25,        # доллар, доходности, Nasdaq, VIX, нефть, иена, ликвидность ФРС
}

# Пороги: значение, при котором сигнал даёт +1 / -1 (между ними линейно)
THRESHOLDS = {
    "etf_flow_3d_musd": 300,       # сумма притоков ETF за 3 дня, млн $
    "coinbase_premium_pct": 0.10,  # премия Coinbase к Binance, %
    "stablecoin_7d_pct": 1.0,      # изменение капитализации стейблкоинов за 7 дней, %
    "funding_hot": 0.03,           # funding за 8ч, %: выше — плечо перегрето
    "dxy_5d_pct": 1.0,
    "us10y_5d_bp": 15,
    "nasdaq_5d_pct": 3.0,
    "oil_5d_pct": 8.0,
    "usdjpy_5d_pct": 2.0,          # резкое укрепление иены = риск сворачивания керри-трейда
    "net_liq_4w_pct": 2.0,
}

# Границы режимов итогового индекса (-100…+100)
REGIMES = [
    (40, "Сильный бычий импульс"),
    (15, "Умеренно бычий фон"),
    (-15, "Нейтрально / нет импульса"),
    (-40, "Умеренно медвежий фон"),
    (-101, "Сильное давление продавцов"),
]

HTTP_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (btc-monitor; personal use)"
