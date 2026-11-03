"""Дашборд: streamlit run dashboard.py"""
import time
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

import storage
from config import GROUP_WEIGHTS, INTERVAL_SECONDS
from presentation import (BULL, NEUTRAL, GROUPS, SIGNALS, NEUTRAL_TXT, ZONES,
                          color, summary, checklist_rows, current_value, verdict)

st.set_page_config(page_title="BTC: единая картина", page_icon="₿", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.gauge-num {font-size: 3.4rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums;}
.gauge-reg {font-size: 1.15rem; margin: .35rem 0 1rem;}
.gauge-track {position: relative; display: flex; height: 14px; border-radius: 7px; overflow: hidden;}
.gauge-mark {position: absolute; top: -5px; width: 4px; height: 24px; border-radius: 2px;
             background: currentColor; transform: translateX(-2px);}
.gauge-wrap {position: relative; padding-top: 5px;}
.gauge-scale {display: flex; justify-content: space-between; font-size: .75rem; opacity: .6; margin-top: .3rem;}
.check {display: flex; gap: .6rem; align-items: baseline; padding: .45rem 0;
        border-bottom: 1px solid rgba(128,128,128,.18);}
.check:last-child {border-bottom: none;}
.check b {font-weight: 600;}
.check small {opacity: .7;}
.tbl-wrap {overflow-x: auto; margin: .25rem 0 1.5rem;}
table.bd {width: 100%; min-width: 760px; border-collapse: collapse; table-layout: fixed; font-size: .92rem;}
table.bd col.c-name {width: 21%;} table.bd col.c-now {width: 29%;}
table.bd col.c-score {width: 22%;} table.bd col.c-mean {width: 28%;}
table.bd th {text-align: left; font-weight: 500; font-size: .8rem; opacity: .65; padding: .5rem .75rem;
             border-bottom: 2px solid rgba(128,128,128,.35);}
table.bd th small {opacity: .8; font-weight: 400;}
table.bd td {padding: .45rem .75rem; border-bottom: 1px solid rgba(128,128,128,.15); vertical-align: middle;}
table.bd tr.grp td {background: rgba(128,128,128,.08); border-top: 1px solid rgba(128,128,128,.3);
                    padding-top: .65rem; padding-bottom: .65rem;}
table.bd tr.grp td b {display: block; font-size: 1rem;}
table.bd tr.grp small, table.bd td small {opacity: .65; font-size: .8rem;}
table.bd td.sig {padding-left: 1.6rem;}
table.bd td.now {font-variant-numeric: tabular-nums; opacity: .85;}
table.bd td.mean {opacity: .85;}
.barcell {display: flex; align-items: center; gap: .6rem;}
.track {position: relative; flex: 1; height: 10px; border-radius: 5px; background: rgba(128,128,128,.12);}
.track.strong {height: 14px; border-radius: 7px;}
.track .axis {position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1px; background: rgba(128,128,128,.6);}
.track .fill {position: absolute; top: 0; bottom: 0; border-radius: 3px;}
.barcell .num {flex: none; width: 3.2rem; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums;}
.dot {flex: none; width: .7rem; height: .7rem; border-radius: 50%; transform: translateY(1px);}
</style>""", unsafe_allow_html=True)


def gauge(value, regime):
    zones = "".join(
        f'<div style="flex:{hi - lo};background:{c};opacity:{a}"></div>' for lo, hi, c, a in ZONES)
    pos = (value + 100) / 2
    return f"""
    <div class="gauge-num" style="color:{color(value / 100 * 3)}">{value:+.0f}</div>
    <div class="gauge-reg">{regime}</div>
    <div class="gauge-wrap"><div class="gauge-track">{zones}</div>
      <div class="gauge-mark" style="left:{pos:.1f}%"></div></div>
    <div class="gauge-scale"><span>−100 продавцы</span><span>0</span><span>покупатели +100</span></div>"""


def bar(v, strong=False):
    """Расходящаяся полоса −1…+1 с подписью значения."""
    w = min(abs(v), 1) * 50
    c = color(v)
    side = f"left:50%;width:{w:.1f}%" if v >= 0 else f"left:{50 - w:.1f}%;width:{w:.1f}%"
    fill = f'<span class="fill" style="{side};background:{c}"></span>' if w >= 1 else \
           f'<span class="fill" style="left:calc(50% - 1.5px);width:3px;background:{c}"></span>'
    return (f'<div class="barcell"><div class="track{" strong" if strong else ""}">'
            f'<span class="axis"></span>{fill}</div>'
            f'<span class="num" style="color:{c}">{v:+.2f}</span></div>')


def breakdown_table(sc, raw, prev_sc):
    from html import escape
    out = ['<div class="tbl-wrap"><table class="bd"><colgroup><col class="c-name"><col class="c-now">'
           '<col class="c-score"><col class="c-mean"></colgroup>'
           '<thead><tr><th>Сигнал</th><th>Сейчас</th><th>Оценка <small>−1 … +1</small></th>'
           '<th>Что это значит</th></tr></thead><tbody>']
    for g, (gname, hint) in GROUPS.items():
        v = sc["groups"].get(g)
        delta = '<small>без изменений за сутки</small>' if prev_sc else '<small>нет данных за сутки</small>'
        if v is not None and prev_sc and prev_sc["groups"].get(g) is not None:
            dd = v - prev_sc["groups"][g]
            if abs(dd) >= 0.01:
                delta = (f'<span style="color:{color(dd * 3)}">{"▲" if dd > 0 else "▼"} '
                         f'{dd:+.2f} за сутки</span>')
        out.append(
            f'<tr class="grp"><td><b>{gname}</b><small>вес {GROUP_WEIGHTS[g]:.0%}</small></td>'
            f'<td>{delta}</td>'
            f'<td>{bar(v, strong=True) if v is not None else "нет данных"}</td>'
            f'<td><small>{escape(hint)}</small></td></tr>')
        for k, (label, pos, neg) in SIGNALS.items():
            sig = sc["signals"].get(k)
            if not sig or sig["group"] != g or sig["value"] is None:
                continue
            sv = sig["value"]
            if k == "leverage":
                meaning = sig["comment"][:1].lower() + sig["comment"][1:]
            else:
                meaning = pos if sv > 0.15 else neg if sv < -0.15 else NEUTRAL_TXT.get(k, "нейтрально")
            out.append(f'<tr><td class="sig">{label}</td><td class="now">{escape(current_value(k, raw))}</td>'
                       f'<td>{bar(sv)}</td><td class="mean">{escape(meaning)}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def history_chart(hist):
    df = pd.DataFrame({
        "Время": [datetime.fromtimestamp(h["ts"]) for h in hist],
        "Индекс": [h["composite"] for h in hist],
        "Цена": [h["raw"].get("trend", {}).get("price") for h in hist],
    })
    bands = alt.Chart(pd.DataFrame([{"lo": lo, "hi": hi, "c": c, "a2": a * .6} for lo, hi, c, a in ZONES])) \
        .mark_rect().encode(y=alt.Y("lo:Q", scale=alt.Scale(domain=[-100, 100])), y2="hi:Q",
                            color=alt.Color("c:N", scale=None), opacity=alt.Opacity("a2:Q", scale=None))
    idx = alt.Chart(df).mark_line(strokeWidth=2.5, color="#3B6FB6").encode(
        x=alt.X("Время:T", title=None, axis=alt.Axis(format="%d.%m %H:%M", labelAngle=0, tickCount=6)),
        y=alt.Y("Индекс:Q", scale=alt.Scale(domain=[-100, 100]), title="Индекс"),
        tooltip=[alt.Tooltip("Время:T", format="%d.%m %H:%M"), alt.Tooltip("Индекс:Q", format="+.0f"),
                 alt.Tooltip("Цена:Q", format=",.0f")])
    price = alt.Chart(df).mark_line(strokeWidth=1.5, strokeDash=[4, 3], color=NEUTRAL).encode(
        x="Время:T", y=alt.Y("Цена:Q", scale=alt.Scale(zero=False), title="Цена BTC, $"))
    return alt.layer(alt.layer(bands, idx), price).resolve_scale(y="independent").properties(height=320)


# ---------- данные ----------
snap = storage.last(1)
if not snap:
    st.info("Данных пока нет. Запустите `python monitor.py --once` и обновите страницу.")
    st.stop()
snap = snap[0]
sc, raw = snap["scored"], snap["raw"]
prev = storage.last(1, before_ts=snap["ts"] - 86400)
prev_sc = prev[0]["scored"] if prev else None

age_min = (time.time() - snap["ts"]) / 60
errors = {k: v["error"] for k, v in raw.items() if isinstance(v, dict) and "error" in v}

# ---------- шапка ----------
st.title("Биткоин: сводная картина")
meta = f"Обновлено {datetime.fromtimestamp(snap['ts']):%d.%m.%Y %H:%M} · данные по {sc['coverage']:.0%} групп"
if errors:
    meta += f" · источников с ошибками: {len(errors)} (внизу страницы)"
st.caption(meta)
if age_min > 2.5 * INTERVAL_SECONDS / 60:
    st.warning(f"Последние данные получены {age_min:.0f} мин назад. Проверьте, запущен ли monitor.py.")

# ---------- главное ----------
left, right = st.columns([5, 6], gap="large")
with left:
    st.markdown(gauge(sc["composite"], sc["regime"]), unsafe_allow_html=True)
    if prev_sc:
        d = sc["composite"] - prev_sc["composite"]
        st.caption(f"За сутки: {d:+.0f} (было {prev_sc['composite']:+.0f}, «{prev_sc['regime']}»)")
with right:
    tr = raw.get("trend", {})
    if "price" in tr:
        st.metric("BTC", f"${tr['price']:,.0f}", f"{tr['chg_24h_pct']:+.2f}% за 24ч")
    for line in summary(sc):
        st.markdown(line)

st.divider()

# ---------- чек-лист ----------
rows = checklist_rows(raw)
passed = sum(r[0] for r in rows)
st.subheader(f"Подтверждение импульса: {passed} из 4 — {verdict(passed)}")
st.caption("Настоящее движение поддерживают одновременно деньги, позиции и цена. "
           "Одно-два условия из четырёх — чаще шум или короткий сквиз.")
html = ""
for ok, title, now, need in rows:
    html += (f'<div class="check"><span class="dot" style="background:{BULL if ok else NEUTRAL};'
             f'opacity:{1 if ok else .45}"></span><div><b>{"✓ " if ok else ""}{title}</b> · {now}'
             f'{"" if ok else f" <small>({need})</small>"}</div></div>')
st.markdown(html, unsafe_allow_html=True)

st.divider()

# ---------- группы ----------
st.subheader("Откуда взялась оценка")
st.caption("Строка группы — её итоговая оценка и изменение за сутки, ниже — сигналы, из которых она сложилась. "
           "Зелёный — за рост, красный — против, серый — нейтрально. Подробно о сигналах — в FEATURES.md.")
st.markdown(breakdown_table(sc, raw, prev_sc), unsafe_allow_html=True)

# ---------- детали во вкладках ----------
tab_hist, tab_vol, tab_src = st.tabs(["История", "Волатильность и прогнозы", "Источники данных"])

with tab_hist:
    hist = storage.history(30)
    if len(hist) > 1:
        st.altair_chart(history_chart(hist), width="stretch")
        st.caption("Синяя линия — индекс на фоне зон режимов, пунктир — цена. Интереснее всего расхождения: "
                   "цена растёт, а индекс падает — рост теряет опору; цена стоит, а индекс растёт — "
                   "под рынок заходят деньги.")
    else:
        st.info("История появится после нескольких запусков монитора.")

with tab_vol:
    a, b = st.columns(2)
    dv = raw.get("dvol", {})
    with a:
        if "dvol" in dv:
            mood = "спокойно" if dv["dvol"] < 50 else "оживлённо" if dv["dvol"] < 60 else "нервно"
            st.metric("Ожидаемая волатильность (DVOL)", f"{dv['dvol']:.1f}",
                      f"{dv['dvol'] - dv['dvol_7d_ago']:+.1f} за неделю", delta_color="off")
            st.caption(f"Рынок опционов ждёт движения: {mood}. Направления этот показатель не говорит. "
                       "Ориентир: ниже 50 — спокойно, выше 60 — нервно.")
        else:
            st.caption("Нет данных DVOL.")
    with b:
        pm = raw.get("polymarket", {}).get("markets", [])
        if pm:
            st.dataframe(pd.DataFrame(pm).rename(columns={"question": "Вопрос", "outcome": "Исход",
                                                          "prob": "Вероятность, %"}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Рынки Polymarket не выбраны. Добавьте slug в POLYMARKET_EVENT_SLUGS в config.py.")

with tab_src:
    names = {"trend": "Цена и тренд (Binance)", "premium": "Coinbase Premium", "deriv": "Деривативы (Binance)",
             "dvol": "DVOL (Deribit)", "stables": "Стейблкоины (DefiLlama)", "etf": "ETF-потоки (Farside)",
             "macro": "Макро (Yahoo)", "liquidity": "Ликвидность ФРС (FRED)", "polymarket": "Polymarket"}
    src = []
    for k, label in names.items():
        v = raw.get(k, {})
        if isinstance(v, dict) and "error" in v:
            status, note = "✗ ошибка", v["error"]
        elif isinstance(v, dict) and v.get("stale"):
            status, note = "◐ из кэша", f"данные на {v.get('as_of', '?')}"
        else:
            status, note = "✓ работает", (v.get("last_date") or v.get("as_of") or "") if isinstance(v, dict) else ""
        src.append({"Источник": label, "Статус": status, "Примечание": note})
    st.dataframe(pd.DataFrame(src), hide_index=True, width="stretch")
