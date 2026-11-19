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

    msgs += background_alerts(raw, scored)

    for m in msgs:
        send(m)
    return msgs


def background_alerts(raw, scored):
    """Фон: устойчивая смена состояния маркера, напоминание о точке проверки, вышедшие данные."""
    from datetime import datetime

    from background import MARKERS, STATES
    from config import INTERVAL_SECONDS

    msgs = []
    bg = scored.get("background") or {}
    hist = storage.last(4)  # текущий замер уже сохранён и идёт первым
    if len(hist) == 4 and bg:
        for k, (name, _) in MARKERS.items():
            sts = [((h["scored"].get("background") or {}).get("markers") or {}).get(k, {}).get("state")
                   for h in hist]
            if sts[0] in STATES and sts[0] != "nodata" and sts[0] == sts[1] == sts[2] \
                    and sts[3] not in (None, "nodata", sts[0]):
                m = bg["markers"][k]
                msgs.append(f"Фон · {name}: {STATES[sts[3]]} → {STATES[sts[0]]}. {m.get('headline', '')}")

    now = time.time()
    for e in (raw.get("bg_calendar") or {}).get("events", []):
        if e.get("imp", 0) >= 2 and now - INTERVAL_SECONDS < e["ts"] - 3 * 3600 <= now:
            t = datetime.fromtimestamp(e["ts"]).strftime("%H:%M")
            msgs.append(f"Через 3 часа, в {t}: {e['title']}" + (" (ориентировочно)" if e.get("approx") else ""))

    prev = hist[1]["raw"] if len(hist) > 1 else {}
    l0, l1 = prev.get("bg_labor") or {}, raw.get("bg_labor") or {}
    if l0.get("as_of") and l1.get("as_of") and l1["as_of"] > l0["as_of"] and l1.get("unrate") is not None:
        msgs.append(f"Вышел отчёт по рынку труда: безработица {l1['unrate']:.1f}%"
                    + (f", правило Сам {l1['sahm']:.2f}" if l1.get("sahm") is not None else ""))
    r0, r1 = prev.get("bg_rates") or {}, raw.get("bg_rates") or {}
    if r0.get("iorb") is not None and r1.get("iorb") is not None and abs(r1["iorb"] - r0["iorb"]) >= 0.1:
        bp = round((r1["iorb"] - r0["iorb"]) * 100)
        msgs.append(f"ФРС {'снизила' if bp < 0 else 'повысила'} ставку на {abs(bp)} б.п.")
    a0 = (prev.get("bg_ai") or {}).get("companies") or {}
    for tic, c in ((raw.get("bg_ai") or {}).get("companies") or {}).items():
        if c.get("filed") and (a0.get(tic) or {}).get("filed") and c["filed"] > a0[tic]["filed"]:
            msgs.append(f"Новый квартальный отчёт {tic} в SEC: обновлены данные по выручке и капексу")
    return msgs
