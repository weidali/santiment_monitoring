"""Дашборд: streamlit run dashboard.py"""
import time
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

import storage
from config import GROUP_WEIGHTS, INTERVAL_SECONDS, THRESHOLDS as T

BULL, BEAR, NEUTRAL = "#2E8B7A", "#C2533F", "#8A929A"

GROUPS = {
    "flows": ("Потоки денег", "Приходят ли реальные деньги: ETF, покупки в США, стейблкоины"),
    "positioning": ("Плечо", "Чем сделано движение: новыми позициями, сквизом или перегретым плечом"),
    "trend": ("Тренд", "Цена относительно средних за 50 и 200 дней"),
    "macro": ("Макро", "Доллар, доходности, акции, страх, нефть, иена, ликвидность ФРС"),
}

# название, что значит «плюс», что значит «минус»
SIGNALS = {
    "etf": ("ETF-потоки", "деньги идут в биткоин-ETF", "деньги уходят из биткоин-ETF"),
    "premium": ("Coinbase Premium", "американцы покупают активнее остальных", "американцы продают"),
    "stables": ("Стейблкоины", "в крипту заходят новые доллары", "доллары выходят из крипты"),
    "leverage": ("Структура плеча", "рост идёт на новых позициях", "позиции давят цену вниз"),
    "funding": ("Funding", "плечо не перегрето", "лонги перегреты, риск пролива"),
    "trend": ("Тренд", "цена выше средних", "цена ниже средних"),
    "dxy": ("Доллар (DXY)", "доллар слабеет", "доллар крепнет"),
    "us10y": ("Доходность 10Y", "доходности снижаются", "доходности растут"),
    "nasdaq": ("Nasdaq", "акции растут", "акции падают"),
    "vix": ("VIX", "на рынке спокойно", "на рынке страх"),
    "oil": ("Нефть", "нефть не давит", "нефть дорожает"),
    "yen": ("Иена", "иена спокойна", "иена резко крепнет"),
    "liquidity": ("Ликвидность ФРС", "ликвидности становится больше", "ликвидность сокращается"),
}

ZONES = [(-100, -40, BEAR, .28), (-40, -15, BEAR, .14), (-15, 15, NEUTRAL, .2),
         (15, 40, BULL, .14), (40, 100, BULL, .28)]

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
.dot {flex: none; width: .7rem; height: .7rem; border-radius: 50%; transform: translateY(1px);}
</style>""", unsafe_allow_html=True)


def color(v):
    return BULL if v > 0.15 else BEAR if v < -0.15 else NEUTRAL


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


def summary(sc):
    """Человеческое резюме: что сильнее всего тянет вверх и вниз с учётом весов."""
    counts = {}
    for s in sc["signals"].values():
        counts[s["group"]] = counts.get(s["group"], 0) + 1
    contrib = []
    for k, s in sc["signals"].items():
        if s["value"] is None or k not in SIGNALS:
            continue
        w = GROUP_WEIGHTS[s["group"]] / counts[s["group"]]
        contrib.append((s["value"] * w, k, s["value"]))
    ups = [SIGNALS[k][1] for c, k, v in sorted(contrib, reverse=True) if v > 0.2][:2]
    downs = [SIGNALS[k][2] for c, k, v in sorted(contrib) if v < -0.2][:2]
    lines = []
    if ups:
        lines.append("**За рост:** " + "; ".join(ups) + ".")
    if downs:
        lines.append("**Против:** " + "; ".join(downs) + ".")
    if not lines:
        lines.append("Сильных сигналов нет ни в одну сторону.")
    return lines


def checklist_rows(raw):
    etf, pr, dv, tr = (raw.get(k, {}) for k in ("etf", "premium", "deriv", "trend"))
    rows = []

    def add(ok, title, now, need):
        rows.append((bool(ok), title, now, need))

    if "streak_days" in etf:
        add(etf["streak_days"] >= 3, "Приток в ETF несколько дней подряд",
            f"серия {etf['streak_days']:+d} дн.", "нужно 3+ дня притоков")
    else:
        add(False, "Приток в ETF несколько дней подряд", "нет данных", "нужно 3+ дня притоков")
    if "premium_pct" in pr:
        add(pr["premium_pct"] > 0, "Американцы покупают", f"премия {pr['premium_pct']:+.3f}%",
            "нужна премия выше 0")
    else:
        add(False, "Американцы покупают", "нет данных", "нужна премия выше 0")
    if "oi_chg_24h_pct" in dv:
        add(dv["oi_chg_24h_pct"] > 0 and dv["funding_pct"] < T["funding_hot"],
            "Новые позиции без перегрева",
            f"OI {dv['oi_chg_24h_pct']:+.1f}% за сутки, funding {dv['funding_pct']:.3f}%",
            f"нужен рост OI и funding ниже {T['funding_hot']}%")
    else:
        add(False, "Новые позиции без перегрева", "нет данных", "нужен рост OI при спокойном funding")
    if "price" in tr:
        add(tr["price"] > tr["ma50"] and tr["vol_ratio"] > 1.2, "Пробой с объёмом",
            f"цена {'выше' if tr['price'] > tr['ma50'] else 'ниже'} MA50, объём x{tr['vol_ratio']:.1f}",
            "нужна цена выше MA50 и объём x1.2+")
    else:
        add(False, "Пробой с объёмом", "нет данных", "нужна цена выше MA50 и объём x1.2+")
    return rows


def group_chart(sc, g):
    """Полосы сигналов одной группы: от −1 (против роста) до +1 (за рост)."""
    gname = GROUPS[g][0]
    rows = []
    for k in SIGNALS:  # фиксированный порядок
        sig = sc["signals"].get(k)
        if not sig or sig["group"] != g or sig["value"] is None:
            continue
        v = sig["value"]
        rows.append({"Группа": gname, "Сигнал": SIGNALS[k][0], "Значение": v, "Детали": sig["comment"],
                     "Смысл": SIGNALS[k][1] if v > 0.15 else SIGNALS[k][2] if v < -0.15 else "нейтрально",
                     "c": color(v), "Подпись": f"{v:+.1f}"})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    base = alt.Chart(df).encode(
        y=alt.Y("Сигнал:N", sort=df["Сигнал"].tolist(), title=None,
                axis=alt.Axis(labelLimit=150, ticks=False, domain=False, labelFontSize=12)),
        tooltip=["Сигнал", alt.Tooltip("Значение:Q", format="+.2f"), "Смысл", "Детали"])
    x = alt.X("Значение:Q", scale=alt.Scale(domain=[-1.25, 1.25]), title=None,
              axis=alt.Axis(values=[-1, 0, 1], format="+.0f", grid=True, labelFontSize=10))
    bars = base.mark_bar(cornerRadius=3, height=12).encode(x=x, color=alt.Color("c:N", scale=None))
    ticks = base.mark_tick(thickness=3, size=12).encode(x=x, color=alt.Color("c:N", scale=None))
    zero = base.mark_rule(opacity=.45).encode(x=alt.datum(0))
    lab_pos = base.mark_text(fontSize=11, fontWeight="bold", align="left", dx=6).encode(
        x=x, text="Подпись:N", color=alt.Color("c:N", scale=None)).transform_filter("datum['Значение'] >= 0")
    lab_neg = base.mark_text(fontSize=11, fontWeight="bold", align="right", dx=-6).encode(
        x=x, text="Подпись:N", color=alt.Color("c:N", scale=None)).transform_filter("datum['Значение'] < 0")
    return alt.layer(zero, bars, ticks, lab_pos, lab_neg).properties(
        height=alt.Step(24)).configure_view(stroke=None)


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
verdict = {4: "импульс подтверждён деньгами", 3: "почти подтверждён", 2: "частично",
           1: "подтверждения нет", 0: "подтверждения нет"}[passed]
st.subheader(f"Подтверждение импульса: {passed} из 4 — {verdict}")
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
st.caption("Каждая полоса — один сигнал от −1 (против роста) до +1 (за рост). "
           "Наведите на полосу, чтобы увидеть пояснение. Подробно о сигналах — в FEATURES.md.")
keys = list(GROUPS)
for pair in (keys[:2], keys[2:]):
    for col, g in zip(st.columns(2, gap="large"), pair):
        name, hint = GROUPS[g]
        v = sc["groups"].get(g)
        delta = None
        if v is not None and prev_sc and prev_sc["groups"].get(g) is not None:
            dd = v - prev_sc["groups"][g]
            delta = f"{dd:+.2f} за сутки" if abs(dd) >= 0.01 else None
        with col:
            st.metric(f"{name} · вес {GROUP_WEIGHTS[g]:.0%}",
                      "нет данных" if v is None else f"{v:+.2f}", delta, help=hint)
            ch = group_chart(sc, g)
            if ch is not None:
                st.altair_chart(ch, width="stretch")

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
