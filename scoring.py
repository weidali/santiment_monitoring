"""Превращает сырые метрики в сигналы от -1 до +1, группирует и сводит в индекс -100…+100."""
from config import THRESHOLDS as T, GROUP_WEIGHTS, REGIMES


def lin(x, full):
    """Линейная шкала: x = +full → +1, x = -full → -1, с обрезкой."""
    if x is None:
        return None
    return max(-1.0, min(1.0, x / full))


def ok(d):
    return isinstance(d, dict) and bool(d) and "error" not in d


def leverage_structure(price_chg, oi_chg, funding):
    """Что стоит за движением цены: новые деньги, плечо или закрытие позиций."""
    hot = funding > T["funding_hot"]
    if price_chg > 0 and oi_chg > 0:
        return (0.0, "Рост на плече: funding перегрет, хрупко") if hot else \
               (1.0, "Рост с притоком новых позиций при спокойном funding")
    if price_chg > 0 and oi_chg <= 0:
        return 0.3, "Рост за счёт закрытия шортов (шорт-сквиз)"
    if price_chg <= 0 and oi_chg > 0:
        return -1.0, "Падение с наращиванием позиций: давят шорты или ловят нож лонги"
    return -0.3, "Падение с закрытием позиций (разгрузка плеча)"


def score(raw):
    sig = {}  # name -> (group, value, comment)

    etf = raw.get("etf", {})
    if ok(etf):
        sig["etf"] = ("flows", lin(etf["sum_3d_musd"], T["etf_flow_3d_musd"]),
                      f"ETF за 3 дня: {etf['sum_3d_musd']:+.0f} млн $, серия {etf['streak_days']:+d} дн.")

    pr = raw.get("premium", {})
    if ok(pr):
        sig["premium"] = ("flows", lin(pr["premium_pct"], T["coinbase_premium_pct"]),
                          f"Coinbase Premium {pr['premium_pct']:+.3f}%")

    st = raw.get("stables", {})
    if ok(st):
        sig["stables"] = ("flows", lin(st["chg_7d_pct"], T["stablecoin_7d_pct"]),
                          f"Стейблкоины за 7 дн. {st['chg_7d_pct']:+.2f}%")

    tr, dv = raw.get("trend", {}), raw.get("deriv", {})
    if ok(tr) and ok(dv):
        v, c = leverage_structure(tr["chg_24h_pct"], dv["oi_chg_24h_pct"], dv["funding_pct"])
        sig["leverage"] = ("positioning", v, c)
        f = dv["funding_pct"]
        fv = -1.0 if f > 2 * T["funding_hot"] else -0.5 if f > T["funding_hot"] else \
             0.3 if f < -0.01 else 0.2  # отрицательный funding = толпа в шортах, топливо для сквиза
        sig["funding"] = ("positioning", fv, f"Funding {f:+.4f}% / 8ч")

    if ok(tr):
        above50, above200 = tr["price"] > tr["ma50"], tr["price"] > tr["ma200"]
        v = 1.0 if above50 and above200 else -1.0 if not above50 and not above200 else 0.0
        if tr["vol_ratio"] > 1.5:
            v = max(-1, min(1, v * 1.2))
        sig["trend"] = ("trend", v, f"Цена {tr['price']:,.0f}; MA50 {tr['ma50']:,.0f}; "
                                    f"MA200 {tr['ma200']:,.0f}; объём x{tr['vol_ratio']:.1f}")

    m = raw.get("macro", {})
    if ok(m):
        if "dxy_5d_pct" in m:
            sig["dxy"] = ("macro", -lin(m["dxy_5d_pct"], T["dxy_5d_pct"]), f"DXY 5д {m['dxy_5d_pct']:+.2f}%")
        if "us10y_5d_bp" in m:
            sig["us10y"] = ("macro", -lin(m["us10y_5d_bp"], T["us10y_5d_bp"]), f"10Y 5д {m['us10y_5d_bp']:+.0f} б.п.")
        if "nasdaq_5d_pct" in m:
            sig["nasdaq"] = ("macro", lin(m["nasdaq_5d_pct"], T["nasdaq_5d_pct"]), f"Nasdaq 5д {m['nasdaq_5d_pct']:+.2f}%")
        if "vix" in m:
            v = m["vix"]
            sig["vix"] = ("macro", 0.5 if v < 15 else 0.0 if v < 20 else -0.5 if v < 25 else -1.0, f"VIX {v:.1f}")
        if "oil_5d_pct" in m:
            sig["oil"] = ("macro", -lin(m["oil_5d_pct"], T["oil_5d_pct"]), f"Нефть 5д {m['oil_5d_pct']:+.1f}%")
        if "usdjpy_5d_pct" in m:
            x = m["usdjpy_5d_pct"]
            v = -1.0 if x < -T["usdjpy_5d_pct"] else 0.0
            sig["yen"] = ("macro", v, f"USD/JPY 5д {x:+.2f}%" + (" — риск керри-трейда" if v < 0 else ""))

    lq = raw.get("liquidity", {})
    if ok(lq):
        sig["liquidity"] = ("macro", lin(lq["chg_4w_pct"], T["net_liq_4w_pct"]),
                            f"Ликвидность ФРС {lq['net_liq_trn']:.2f} трлн $, 4 нед. {lq['chg_4w_pct']:+.2f}% "
                            f"(на {lq['as_of']}{', из кэша' if lq.get('stale') else ''})")

    # Сводим по группам; если группа пустая, её вес перераспределяется
    groups = {}
    for name, (g, v, _) in sig.items():
        if v is not None:
            groups.setdefault(g, []).append(v)
    group_scores = {g: sum(vs) / len(vs) for g, vs in groups.items()}
    wsum = sum(GROUP_WEIGHTS[g] for g in group_scores) or 1
    composite = 100 * sum(GROUP_WEIGHTS[g] * s for g, s in group_scores.items()) / wsum

    regime = next(label for lim, label in REGIMES if composite >= lim)
    confirmed = confirmation(raw)
    coverage = len(group_scores) / len(GROUP_WEIGHTS)

    return {"composite": round(composite, 1) + 0.0, "regime": regime, "confirmed": confirmed,
            "coverage": coverage, "groups": group_scores,
            "signals": {k: {"group": g, "value": v, "comment": c} for k, (g, v, c) in sig.items()}}


def confirmation(raw):
    """Чек-лист «настоящего» импульса: все условия сразу, а не один сигнал."""
    etf, pr, dv, tr = raw.get("etf", {}), raw.get("premium", {}), raw.get("deriv", {}), raw.get("trend", {})
    checks = {
        "ETF: приток 3+ дня подряд": ok(etf) and etf["streak_days"] >= 3,
        "Coinbase Premium положительный": ok(pr) and pr["premium_pct"] > 0,
        "OI растёт, funding не перегрет": ok(dv) and dv["oi_chg_24h_pct"] > 0 and dv["funding_pct"] < T["funding_hot"],
        "Цена выше MA50 на повышенном объёме": ok(tr) and tr["price"] > tr["ma50"] and tr["vol_ratio"] > 1.2,
    }
    return {"checks": checks, "all_passed": all(checks.values())}
