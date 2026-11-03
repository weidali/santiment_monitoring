"""Общие для дашборда и iPad-экрана тексты, цвета и расчёты (без зависимости от Streamlit)."""
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
