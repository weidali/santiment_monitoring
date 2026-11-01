"""Дашборд: streamlit run dashboard.py"""
from datetime import datetime

import pandas as pd
import streamlit as st

import storage

GROUP_NAMES = {"flows": "Потоки денег", "positioning": "Плечо и позиции",
               "trend": "Тренд", "macro": "Макро"}

st.set_page_config(page_title="BTC: единая картина", layout="wide")

snap = storage.last(1)
if not snap:
    st.info("Данных пока нет. Запустите `python monitor.py --once`, затем обновите страницу.")
    st.stop()
snap = snap[0]
sc, raw = snap["scored"], snap["raw"]

st.title("Биткоин: сводная картина сигналов")
st.caption(f"Обновлено {datetime.fromtimestamp(snap['ts']):%d.%m.%Y %H:%M} · "
           f"покрытие данных {sc['coverage']:.0%}")

c1, c2, c3 = st.columns([1, 2, 2])
c1.metric("Индекс", f"{sc['composite']:+.0f}", help="−100 … +100, взвешенная сумма групп")
c2.subheader(sc["regime"])
price = raw.get("trend", {}).get("price")
if price:
    c3.metric("BTC", f"${price:,.0f}", f"{raw['trend']['chg_24h_pct']:+.2f}% за 24ч")

st.markdown("#### Чек-лист подтверждённого импульса")
for name, passed in sc["confirmed"]["checks"].items():
    st.write(("✅ " if passed else "⬜ ") + name)

st.markdown("#### Группы")
cols = st.columns(len(GROUP_NAMES))
for col, (g, label) in zip(cols, GROUP_NAMES.items()):
    v = sc["groups"].get(g)
    col.metric(label, "нет данных" if v is None else f"{v:+.2f}")
    for s in sc["signals"].values():
        if s["group"] == g:
            col.caption(f"{s['value']:+.1f} · {s['comment']}")

hist = storage.history(30)
if len(hist) > 1:
    st.markdown("#### Индекс и цена за 30 дней")
    df = pd.DataFrame({
        "время": [datetime.fromtimestamp(h["ts"]) for h in hist],
        "индекс": [h["composite"] for h in hist],
        "цена": [h["raw"].get("trend", {}).get("price") for h in hist],
    }).set_index("время")
    a, b = st.columns(2)
    a.line_chart(df["индекс"])
    b.line_chart(df["цена"])

dv = raw.get("dvol", {})
if "dvol" in dv:
    st.markdown("#### Ожидаемая волатильность (Deribit DVOL)")
    st.write(f"{dv['dvol']:.1f} (неделю назад {dv['dvol_7d_ago']:.1f}). "
             "Показывает, насколько сильное движение закладывает рынок опционов, но не его направление.")

pm = raw.get("polymarket", {}).get("markets", [])
if pm:
    st.markdown("#### Рынки предсказаний")
    st.dataframe(pd.DataFrame(pm).rename(columns={"question": "вопрос", "outcome": "исход",
                                                  "prob": "вероятность, %"}), hide_index=True)

errors = {k: v["error"] for k, v in raw.items() if isinstance(v, dict) and "error" in v}
if errors:
    with st.expander(f"Источники с ошибками: {len(errors)}"):
        for k, e in errors.items():
            st.write(f"**{k}**: {e}")
