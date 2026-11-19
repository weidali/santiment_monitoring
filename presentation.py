"""Общие тексты, цвета и расчёты для веб-экрана (app.py)."""
from config import GROUP_WEIGHTS, THRESHOLDS as T

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

NEUTRAL_TXT = {
    "trend": "цена между средними, тренда нет",
    "vix": "умеренный уровень страха",
    "yen": "иена спокойна",
    "etf": "потоки в ETF около нуля",
    "premium": "американцы покупают наравне с остальными",
    "stables": "приток долларов почти не меняется",
    "funding": "плечо в норме",
    "dxy": "доллар почти не меняется",
    "us10y": "доходности почти не меняются",
    "nasdaq": "акции почти не меняются",
    "oil": "нефть почти не меняется",
    "liquidity": "ликвидность почти не меняется",
}

ZONES = [(-100, -40, BEAR, .28), (-40, -15, BEAR, .14), (-15, 15, NEUTRAL, .2),
         (15, 40, BULL, .14), (40, 100, BULL, .28)]

def color(v):
    return BULL if v > 0.15 else BEAR if v < -0.15 else NEUTRAL


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


def current_value(k, raw):
    """Короткое фактическое значение метрики для колонки «Сейчас»."""
    etf, pr, st_, dv, tr, m, lq = (raw.get(x, {}) for x in
                                   ("etf", "premium", "stables", "deriv", "trend", "macro", "liquidity"))
    try:
        return {
            "etf": lambda: f"{etf['sum_3d_musd']:+,.0f} млн $ за 3 дня, серия {etf['streak_days']:+d} дн.",
            "premium": lambda: f"{pr['premium_pct']:+.3f}%",
            "stables": lambda: f"{st_['chg_7d_pct']:+.2f}% за 7 дн.",
            "leverage": lambda: f"цена {tr['chg_24h_pct']:+.1f}%, OI {dv['oi_chg_24h_pct']:+.1f}% за сутки",
            "funding": lambda: f"{dv['funding_pct']:.4f}% за 8 ч",
            "trend": lambda: (f"{'выше' if tr['price'] > tr['ma50'] else 'ниже'} MA50, "
                              f"{'выше' if tr['price'] > tr['ma200'] else 'ниже'} MA200, объём x{tr['vol_ratio']:.1f}"),
            "dxy": lambda: f"{m['dxy']:.1f}, {m['dxy_5d_pct']:+.2f}% за 5 дн.",
            "us10y": lambda: f"{m['us10y']:.2f}%, {m['us10y_5d_bp']:+.0f} б.п. за 5 дн.",
            "nasdaq": lambda: f"{m['nasdaq_5d_pct']:+.2f}% за 5 дн.",
            "vix": lambda: f"{m['vix']:.1f}",
            "oil": lambda: f"{m['oil_5d_pct']:+.1f}% за 5 дн.",
            "yen": lambda: f"USD/JPY {m['usdjpy_5d_pct']:+.2f}% за 5 дн.",
            "liquidity": lambda: (f"{lq['net_liq_trn']:.2f} трлн $, {lq['chg_4w_pct']:+.2f}% за 4 нед."
                                  + (" (из кэша)" if lq.get("stale") else "")),
        }[k]()
    except (KeyError, TypeError):
        return "—"


def verdict(passed):
    return {4: "импульс подтверждён деньгами", 3: "почти подтверждён", 2: "частично",
            1: "подтверждения нет", 0: "подтверждения нет"}[passed]


FNG_RU = {"Extreme Fear": "крайний страх", "Fear": "страх", "Neutral": "нейтрально",
          "Greed": "жадность", "Extreme Greed": "крайняя жадность"}


def dvol_mood(v):
    return "спокойно" if v < 50 else "оживлённо" if v < 60 else "нервно"


SHORT_REGIME = {"Сильный бычий импульс": "сильно бычий", "Умеренно бычий фон": "умеренно бычий",
                "Нейтрально / нет импульса": "нейтральный", "Умеренно медвежий фон": "умеренно медвежий",
                "Сильное давление продавцов": "сильно медвежий"}
REGIME_HOLD = 3  # смена режима засчитывается, если новый режим держится 3 замера подряд (≈45 мин)


def derive_events(hist, limit=6):
    """События восстанавливаются из сохранённой истории замеров, поэтому прошлое не теряется:
    устойчивые смены режима, подтверждение импульса, перегрев funding, крупные дни по ETF.
    Возвращает [(ts, направление -1/0/+1, текст)], новые сверху."""
    ev, prev, etf_seen = [], None, set()
    hot = 2 * T["funding_hot"]
    stable, stable_val, cand, cand_n, cand_ts = None, None, None, 0, None
    for h in hist:
        sc, raw, ts = h["scored"], h["raw"], h["ts"]
        reg = sc["regime"]
        if stable is None:
            stable, stable_val = reg, sc["composite"]
        elif reg == stable:
            cand, cand_n = None, 0
        else:
            if reg != cand:
                cand, cand_n, cand_ts = reg, 0, ts
            cand_n += 1
            if cand_n >= REGIME_HOLD:
                d = 1 if sc["composite"] > stable_val else -1
                ev.append((cand_ts, d, f"Режим: {SHORT_REGIME.get(stable, stable)} → "
                                       f"{SHORT_REGIME.get(reg, reg)}"))
                stable, stable_val, cand, cand_n = reg, sc["composite"], None, 0
        if prev:
            psc, praw = prev["scored"], prev["raw"]
            was, now = psc["confirmed"]["all_passed"], sc["confirmed"]["all_passed"]
            if now and not was:
                ev.append((ts, 1, "Импульс подтверждён: все 4 условия"))
            elif was and not now:
                ev.append((ts, 0, "Подтверждение импульса пропало"))
            f0 = praw.get("deriv", {}).get("funding_pct")
            f1 = raw.get("deriv", {}).get("funding_pct")
            if f0 is not None and f1 is not None and f0 <= hot < f1:
                ev.append((ts, -1, f"Funding перегрет: {f1:.3f}%"))
        etf = raw.get("etf", {})
        if "last_date" in etf and etf["last_date"] not in etf_seen:
            etf_seen.add(etf["last_date"])
            if prev and abs(etf.get("last_day_musd", 0)) >= 500:
                x = etf["last_day_musd"]
                ev.append((ts, 1 if x > 0 else -1, f"ETF за день: {x:+,.0f} млн $".replace(",", " ")))
        prev = h
    ev += background_events(hist)
    return sorted(ev, key=lambda e: e[0], reverse=True)[:limit]


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]


def _price(h):
    return (h["raw"].get("trend") or {}).get("price")


def btc_reaction(hist, ts):
    """Как BTC отреагировал на событие: за сутки после него или с тех пор, если сутки не прошли."""
    before = [h for h in hist if h["ts"] <= ts and _price(h)]
    after = [h for h in hist if h["ts"] >= ts + 86400 and _price(h)]
    if not before:
        return ""
    p0 = _price(before[-1])
    if after:
        return f" · BTC за сутки после: {(_price(after[0]) / p0 - 1) * 100:+.1f}%"
    latest = [h for h in hist if _price(h)]
    if latest and latest[-1]["ts"] > ts:
        return f" · BTC с тех пор: {(_price(latest[-1]) / p0 - 1) * 100:+.1f}%"
    return ""


def background_events(hist):
    """События медленного фона: смена состояния маркеров, выход отчёта по рынку труда,
    решения ФРС, новые квартальные отчёты ИИ-компаний. У событий-точек проверки — реакция BTC."""
    from background import MARKERS, STATES
    from config import FOMC_DECISIONS
    import macro_sources

    ev = []
    order = {"ok": 0, "warn": 1, "stress": 2}
    stable, cand = {}, {}
    prev = None
    for h in hist:
        ts, raw = h["ts"], h["raw"]
        markers = ((h["scored"].get("background") or {}).get("markers") or {})
        for k, m in markers.items():
            st = m.get("state")
            if st not in order:
                continue
            if k not in stable:
                stable[k] = st
            elif st == stable[k]:
                cand.pop(k, None)
            else:
                c = cand.get(k)
                if not c or c[0] != st:
                    c = cand[k] = [st, 0, ts]
                c[1] += 1
                if c[1] >= REGIME_HOLD:
                    d = 1 if order[st] < order[stable[k]] else -1
                    ev.append((c[2], d, f"{MARKERS[k][0]}: {STATES[stable[k]]} → {STATES[st]}"))
                    stable[k] = st
                    cand.pop(k, None)
        if prev:
            lab0, lab1 = prev["raw"].get("bg_labor") or {}, raw.get("bg_labor") or {}
            if lab0.get("as_of") and lab1.get("as_of") and lab1["as_of"] > lab0["as_of"] \
                    and lab1.get("unrate") is not None:
                mon = MONTHS[int(lab1["as_of"][5:7]) - 1]
                dun = lab1["unrate"] - (lab0.get("unrate") or lab1["unrate"])
                txt = f"Рынок труда за {mon}: безработица {lab1['unrate']:.1f}% ({dun:+.1f})"
                if lab1.get("payrolls_3m_k") is not None:
                    txt += f", {lab1['payrolls_3m_k']:+.0f} тыс. мест/мес."
                ev.append((ts, -1 if dun > 0.05 else 1 if dun < -0.05 else 0, txt + btc_reaction(hist, ts)))
            a0 = (prev["raw"].get("bg_ai") or {}).get("companies") or {}
            a1 = (raw.get("bg_ai") or {}).get("companies") or {}
            for tic, c in a1.items():
                old = a0.get(tic) or {}
                if c.get("filed") and old.get("filed") and c["filed"] > old["filed"]:
                    g = (c["revenue"] / c["revenue_prev"] - 1) * 100 if c.get("revenue") and c.get("revenue_prev") \
                        else None
                    txt = f"Отчёт {tic}" + ("" if g is None else f": выручка за год {g:+.0f}%")
                    ev.append((ts, 0, txt + btc_reaction(hist, ts)))
        prev = h
    # решения ФРС: время из расписания, итог — по изменению ставки по резервам (IORB)
    if hist:
        t0, t1 = hist[0]["ts"], hist[-1]["ts"]
        for day, decision in FOMC_DECISIONS:
            if not decision:
                continue
            ts = macro_sources.ny_ts(day, "14:00")
            if not t0 <= ts <= t1:
                continue
            before = [(h["raw"].get("bg_rates") or {}).get("iorb") for h in hist if h["ts"] <= ts]
            after = [(h["raw"].get("bg_rates") or {}).get("iorb") for h in hist if h["ts"] >= ts + 30 * 3600]
            before, after = [x for x in before if x is not None], [x for x in after if x is not None]
            if before and after:
                bp = round((after[-1] - before[-1]) * 100)
                txt = ("ФРС оставила ставку без изменений" if bp == 0 else
                       f"ФРС {'снизила' if bp < 0 else 'повысила'} ставку на {abs(bp)} б.п.")
                d = 1 if bp < 0 else -1 if bp > 0 else 0
            else:
                txt, d = "Решение ФРС по ставке", 0
            ev.append((ts, d, txt + btc_reaction(hist, ts)))
    return ev
