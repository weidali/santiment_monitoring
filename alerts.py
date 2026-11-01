"""Правила уведомлений: смена режима, подтверждённый импульс, экстремумы, резкие сдвиги на Polymarket."""
import time

import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, POLYMARKET_ALERT_PP, THRESHOLDS as T
import storage


def send(text):
    print("[ALERT]", text)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
        except Exception as e:  # noqa: BLE001
            print("Telegram error:", e)


def check(raw, scored):
    msgs = []
    prev = storage.last(2)
    prev = prev[1] if len(prev) > 1 else None

    if prev and prev["regime"] != scored["regime"]:
        msgs.append(f"Режим сменился: {prev['regime']} → {scored['regime']} (индекс {scored['composite']:+.0f})")

    if scored["confirmed"]["all_passed"] and not (prev and prev["scored"]["confirmed"]["all_passed"]):
        msgs.append("Все четыре условия подтверждённого импульса выполнены одновременно")

    dv = raw.get("deriv", {})
    if "funding_pct" in dv and dv["funding_pct"] > 2 * T["funding_hot"]:
        msgs.append(f"Funding перегрет: {dv['funding_pct']:.4f}% — риск каскада ликвидаций лонгов")

    etf = raw.get("etf", {})
    if "last_day_musd" in etf and abs(etf["last_day_musd"]) > 500:
        msgs.append(f"Крупный день по ETF: {etf['last_day_musd']:+.0f} млн $ ({etf['last_date']})")

    day_ago = storage.last(1, before_ts=int(time.time()) - 86400)
    if day_ago:
        old = {m["question"]: m["prob"] for m in day_ago[0]["raw"].get("polymarket", {}).get("markets", [])}
        for m in raw.get("polymarket", {}).get("markets", []):
            if m["question"] in old and abs(m["prob"] - old[m["question"]]) >= POLYMARKET_ALERT_PP:
                msgs.append(f"Polymarket: «{m['question']}» {old[m['question']]:.0f}% → {m['prob']:.0f}% за сутки")

    for m in msgs:
        send(m)
    return msgs
