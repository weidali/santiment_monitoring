"""Сборщики медленного макро-фона: рынок труда, ставки, доллар, кредит, ИИ-компании, структура рынка,
а также календарь точек проверки.

Эти данные выходят раз в день, неделю, месяц или квартал, поэтому каждый источник кэшируется
на диске (папка .cache) и опрашивается не чаще, чем указано в BG_CACHE_HOURS. Если источник
не ответил, используется последнее сохранённое значение с пометкой stale.
"""
import calendar as cal
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from config import (AI_BUYERS, AI_SUPPLIER, BASE_DIR, BG_CACHE_HOURS, FOMC_DECISIONS, RELEASES,
                    SEC_USER_AGENT)
from sources import S, _fred, _get, safe

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001  (на очень старых системах без базы часовых поясов)
    NY = None

CACHE_DIR = BASE_DIR / ".cache"


# ================================================================ кэш
def cached(key, hours, fn):
    """Вернуть данные из кэша, если они свежие; иначе вызвать fn(). При сбое — старый кэш со stale."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    old = None
    try:
        old = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        pass
    now = time.time()
    if old and now - old.get("ts", 0) < hours * 3600:
        return old["data"]
    # после неудачи не повторять каждые 15 минут
    if old and now - old.get("failed_at", 0) < 3600 and "data" in old:
        return {**old["data"], "stale": True} if isinstance(old["data"], dict) else old["data"]
    try:
        data = fn()
        path.write_text(json.dumps({"ts": now, "data": data}, default=str))
        return data
    except Exception:
        if old and "data" in old:
            path.write_text(json.dumps({**old, "failed_at": now}, default=str))
            return {**old["data"], "stale": True} if isinstance(old["data"], dict) else old["data"]
        raise


def fred(series):
    """Ряд FRED как список [(дата 'YYYY-MM-DD', значение)], с дисковым кэшем."""
    def load():
        s = _fred(series)
        return [[d.strftime("%Y-%m-%d"), float(v)] for d, v in s.items()]
    return cached(f"fred_{series}", BG_CACHE_HOURS["fred"], load)


class Fetch:
    """Загружает несколько рядов FRED: сбой одного ряда не роняет весь маркер."""
    def __init__(self):
        self.missing = []

    def __call__(self, series):
        try:
            return fred(series)
        except Exception:  # noqa: BLE001
            self.missing.append(series)
            return []

    def result(self, data, required):
        if not any(data.get(k) is not None for k in required):
            raise RuntimeError("FRED не ответил: " + ", ".join(self.missing or ["нет данных"]))
        if self.missing:
            data["missing"] = self.missing
        return data


def last(rows):
    return rows[-1][1] if rows else None


def ago(rows, days):
    """Значение ряда примерно `days` дней назад от последней точки."""
    if not rows:
        return None
    target = datetime.strptime(rows[-1][0], "%Y-%m-%d") - timedelta(days=days)
    best = None
    for d, v in rows:
        if datetime.strptime(d, "%Y-%m-%d") <= target:
            best = v
        else:
            break
    return best


def pct(a, b):
    return None if a is None or b in (None, 0) else (a / b - 1) * 100


# ================================================================ рынок труда
@safe
def labor():
    F = Fetch()
    un, sahm, icsa = F("UNRATE"), F("SAHMREALTIME"), F("ICSA")
    ahe, cpi, pay = F("CES0500000003"), F("CPIAUCSL"), F("PAYEMS")
    claims_4w = sum(v for _, v in icsa[-4:]) / 4 if len(icsa) >= 4 else None
    claims_old = [v for _, v in icsa[-30:-26]]
    claims_old_4w = sum(claims_old) / len(claims_old) if claims_old else None
    ahe_yoy, cpi_yoy = pct(last(ahe), ago(ahe, 365)), pct(last(cpi), ago(cpi, 365))
    pay_3m = (pay[-1][1] - pay[-4][1]) / 3 if len(pay) >= 4 else None  # тыс. рабочих мест в месяц
    return F.result({
        "unrate": last(un), "unrate_12m": (last(un) - ago(un, 365)) if un and ago(un, 365) is not None else None,
        "sahm": last(sahm), "claims_4w": claims_4w, "claims_chg_6m_pct": pct(claims_4w, claims_old_4w),
        "real_wage_yoy": (ahe_yoy - cpi_yoy) if ahe_yoy is not None and cpi_yoy is not None else None,
        "wage_yoy": ahe_yoy, "cpi_yoy": cpi_yoy, "payrolls_3m_k": pay_3m,
        "as_of": un[-1][0] if un else None, "claims_as_of": icsa[-1][0] if icsa else None,
    }, ["unrate", "sahm", "claims_4w"])


# ================================================================ ставки
@safe
def rates():
    F = Fetch()
    y2, y10, curve = F("DGS2"), F("DGS10"), F("T10Y2Y")
    real10, ff, iorb = F("DFII10"), F("DFF"), F("IORB")
    n = 21  # ~ месяц торговых дней
    d2 = (y2[-1][1] - y2[-1 - n][1]) * 100 if len(y2) > n else None
    d10 = (y10[-1][1] - y10[-1 - n][1]) * 100 if len(y10) > n else None
    year = [v for d, v in curve if d >= (date.today() - timedelta(days=365)).isoformat()]
    return F.result({
        "y2": last(y2), "y10": last(y10), "curve_bp": last(curve) * 100 if curve else None,
        "curve_min_1y_bp": min(year) * 100 if year else None,
        "d2_1m_bp": d2, "d10_1m_bp": d10, "real10": last(real10),
        "ff": last(ff), "iorb": last(iorb),
        "y2_minus_ff_bp": (last(y2) - last(ff)) * 100 if y2 and ff else None,
        "as_of": y10[-1][0] if y10 else None,
    }, ["y2", "y10"])


# ================================================================ доллар и долларовое фондирование
@safe
def dollar():
    F = Fetch()
    broad, sofr, iorb = F("DTWEXBGS"), F("SOFR"), F("IORB")
    srf, swaps = F("RPONTSYD"), F("SWPT")
    iorb_map = dict(iorb)
    spread = [(d, (v - iorb_map[d]) * 100) for d, v in sofr if d in iorb_map]
    return F.result({
        "broad": last(broad), "broad_1m_pct": pct(last(broad), ago(broad, 30)),
        "sofr_iorb_bp": spread[-1][1] if spread else None,
        "sofr_iorb_max5_bp": max(v for _, v in spread[-5:]) if spread else None,
        "srf_bn": last(srf), "srf_max5_bn": max(v for _, v in srf[-5:]) if srf else None,
        "swaps_bn": last(swaps) / 1000 if swaps else None,  # SWPT в млн $
        "as_of": broad[-1][0] if broad else None,
    }, ["broad", "sofr_iorb_bp"])


# ================================================================ кредит
@safe
def credit():
    F = Fetch()
    hy, ig, ccc, sloos = F("BAMLH0A0HYM2"), F("BAMLC0A0CM"), F("BAMLH0A3HYC"), F("DRTSCILM")
    year = [v for d, v in hy if d >= (date.today() - timedelta(days=365)).isoformat()]
    return F.result({
        "hy": last(hy), "hy_1m_bp": (last(hy) - ago(hy, 30)) * 100 if hy and ago(hy, 30) is not None else None,
        "hy_min_1y": min(year) if year else None,
        "ig": last(ig), "ccc": last(ccc),
        "ccc_1m_bp": (last(ccc) - ago(ccc, 30)) * 100 if ccc and ago(ccc, 30) is not None else None,
        "sloos": last(sloos), "sloos_as_of": sloos[-1][0] if sloos else None,
        "as_of": hy[-1][0] if hy else None,
    }, ["hy", "ig"])


# ================================================================ ИИ-компании (SEC EDGAR)
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements"],
}


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def edgar_facts(cik, concept):
    """Факты одного показателя из отчётов 10-K/10-Q, без повторов (берётся последняя подача)."""
    r = S.get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{concept}.json",
              headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    time.sleep(0.15)  # SEC просит не больше 10 запросов в секунду
    if r.status_code == 404:
        return []
    r.raise_for_status()
    best = {}
    for f in r.json().get("units", {}).get("USD", []):
        if "start" not in f or f.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
            continue
        k = (f["start"], f["end"])
        if k not in best or f.get("filed", "") > best[k].get("filed", ""):
            best[k] = f
    out = []
    for f in best.values():
        s, e = _d(f["start"]), _d(f["end"])
        out.append({"start": s, "end": e, "dur": (e - s).days, "val": float(f["val"]), "filed": f.get("filed")})
    return out


def ttm_at(facts, end):
    """Сумма за последние 12 месяцев на дату end: годовой отчёт или «год + текущий YTD − прошлый YTD»."""
    at = [f for f in facts if f["end"] == end]
    for f in at:
        if 350 <= f["dur"] <= 380:
            return f["val"]
    ytd = [f for f in at if 80 <= f["dur"] < 350]
    if not ytd:
        return None
    y = max(ytd, key=lambda f: f["dur"])
    annual = [a for a in facts if 350 <= a["dur"] <= 380 and 0 <= (y["start"] - a["end"]).days <= 10]
    prior = [p for p in facts if abs(p["dur"] - y["dur"]) <= 12 and 355 <= (end - p["end"]).days <= 375]
    if not annual or not prior:
        return None
    a = min(annual, key=lambda a: (y["start"] - a["end"]).days)
    p = min(prior, key=lambda p: abs((end - p["end"]).days - 365))
    return a["val"] + y["val"] - p["val"]


def ttm_pair(facts):
    """(TTM сейчас, TTM год назад, дата окончания периода, дата подачи отчёта)."""
    ends = sorted({f["end"] for f in facts if f["dur"] >= 80}, reverse=True)
    for e in ends[:3]:
        now = ttm_at(facts, e)
        prev_ends = [x for x in ends if 355 <= (e - x).days <= 375]
        prev = ttm_at(facts, prev_ends[0]) if prev_ends else None
        if now is not None and prev is not None:
            filed = max((f["filed"] or "") for f in facts if f["end"] == e)
            return now, prev, e, filed
    return None


def company(cik):
    out = {}
    for metric, names in CONCEPTS.items():
        best = None
        for name in names:
            pair = ttm_pair(edgar_facts(cik, name))
            if pair and (best is None or pair[2] > best[2]):
                best = pair
        if best:
            now, prev, end, filed = best
            out[metric] = now
            out[metric + "_prev"] = prev
            out["end"] = max(out.get("end", ""), end.isoformat())
            out["filed"] = max(out.get("filed", ""), filed or "")
    return out


@safe
def ai_companies():
    if not SEC_USER_AGENT:
        raise RuntimeError('Укажите SEC_USER_AGENT в .env, например: SEC_USER_AGENT="Ivan ivan@mail.ru"')
    comps = {}
    for tic, cik in {**AI_BUYERS, **AI_SUPPLIER}.items():
        try:
            comps[tic] = cached(f"edgar_{tic}", BG_CACHE_HOURS["edgar"], lambda c=cik: company(c))
        except Exception as e:  # noqa: BLE001
            comps[tic] = {"error": str(e)[:150]}

    def agg(key):
        vals = [comps[t].get(key) for t in AI_BUYERS if comps.get(t, {}).get(key) is not None]
        return sum(vals) if vals else None

    complete = [t for t in AI_BUYERS if all(comps.get(t, {}).get(k) is not None
                                            for k in ("revenue", "ocf", "capex"))]

    def agg_c(key):
        return sum(comps[t][key] for t in complete) if complete else None

    b = {k: agg_c(k) for k in ("revenue", "revenue_prev", "ocf", "ocf_prev", "capex", "capex_prev")}
    if complete:
        b["fcf"], b["fcf_prev"] = b["ocf"] - b["capex"], b["ocf_prev"] - b["capex_prev"]
    nv = comps.get("NVDA", {})
    return {
        "companies": comps, "buyers_used": complete,
        "rev_yoy": pct(b.get("revenue"), b.get("revenue_prev")),
        "ocf_yoy": pct(b.get("ocf"), b.get("ocf_prev")),
        "capex_yoy": pct(b.get("capex"), b.get("capex_prev")),
        "capex_bn": b["capex"] / 1e9 if b.get("capex") else None,
        "fcf_bn": b["fcf"] / 1e9 if "fcf" in b else None,
        "fcf_yoy": (b["fcf"] / b["fcf_prev"] - 1) * 100 if b.get("fcf_prev") and b["fcf_prev"] > 0 else None,
        "capex_to_ocf": b["capex"] / b["ocf"] if b.get("ocf") else None,
        "nvda_rev_yoy": pct(nv.get("revenue"), nv.get("revenue_prev")),
        "latest_filed": max((c.get("filed") or "" for c in comps.values()), default=""),
        "unused": agg("revenue") is None,
    }


# ================================================================ структура рынка
def yahoo_closes(ticker, rng="1y"):
    def load():
        r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", range=rng, interval="1d").json()
        res = r["chart"]["result"][0]
        rows = []
        for ts, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
            if c is not None:
                rows.append([datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), float(c)])
        return {"rows": rows}
    return cached(f"yahoo_{ticker.replace('^', '_')}", BG_CACHE_HOURS["yahoo"], load)["rows"]


def ratio_chg(a, b, n=21):
    """Изменение отношения a/b за n торговых дней, %."""
    bm = dict(b)
    pairs = [(d, v / bm[d]) for d, v in a if d in bm and bm[d]]
    if len(pairs) <= n:
        return None
    return (pairs[-1][1] / pairs[-1 - n][1] - 1) * 100


def corr(a, b, n=30):
    bm = dict(b)
    common = [(v, bm[d]) for d, v in a if d in bm]
    if len(common) < n + 1:
        return None
    s = pd.DataFrame(common[-(n + 1):], columns=["a", "b"]).pct_change().dropna()
    return float(s["a"].corr(s["b"]))


@safe
def structure():
    ndx, qqq, qqew = yahoo_closes("^IXIC"), yahoo_closes("QQQ"), yahoo_closes("QQEW")
    spy, rsp, iwm, btc = yahoo_closes("SPY"), yahoo_closes("RSP"), yahoo_closes("IWM"), yahoo_closes("BTC-USD")
    hi = max(v for _, v in ndx[-252:]) if ndx else None
    out = {
        "ndx_dd_pct": (1 - ndx[-1][1] / hi) * 100 if hi else None,
        "ndx_1m_pct": pct(ndx[-1][1], ndx[-22][1]) if len(ndx) > 22 else None,
        "rsp_spy_1m": ratio_chg(rsp, spy), "qqew_qqq_1m": ratio_chg(qqew, qqq), "iwm_qqq_1m": ratio_chg(iwm, qqq),
        "btc_ndx_corr30": corr(btc, ndx),
        "as_of": ndx[-1][0] if ndx else None,
    }
    try:
        g = _get("https://api.coingecko.com/api/v3/global").json()["data"]
        out["btc_dom"] = float(g["market_cap_percentage"]["btc"])
        out["crypto_mcap_tn"] = float(g["total_market_cap"]["usd"]) / 1e12
    except Exception as e:  # noqa: BLE001
        out["btc_dom_error"] = str(e)[:120]
    return out


# ================================================================ календарь точек проверки
def ny_ts(day, hhmm):
    """Метка времени UTC для даты и времени по Нью-Йорку."""
    h, m = map(int, hhmm.split(":"))
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=h, minute=m)
    if NY is None:  # приблизительно: летнее время с марта по ноябрь
        off = 4 if 3 <= d.month <= 10 else 5
        return (d + timedelta(hours=off)).replace(tzinfo=timezone.utc).timestamp()
    return d.replace(tzinfo=NY).timestamp()


def fred_release_dates(release_id):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return None
    r = S.get("https://api.stlouisfed.org/fred/release/dates",
              params={"release_id": release_id, "api_key": key, "file_type": "json",
                      "include_release_dates_with_no_data": "true", "sort_order": "asc",
                      "realtime_start": (date.today() - timedelta(days=45)).isoformat(),
                      "realtime_end": "9999-12-31", "limit": 1000}, timeout=30)
    r.raise_for_status()
    lo, hi = (date.today() - timedelta(days=45)).isoformat(), (date.today() + timedelta(days=150)).isoformat()
    return sorted({x["date"] for x in r.json().get("release_dates", []) if lo <= x["date"] <= hi})


def first_friday(y, m):
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def last_friday(y, m):
    d = date(y, m, cal.monthrange(y, m)[1])
    return d - timedelta(days=(d.weekday() - 4) % 7)


@safe
def checkpoints():
    def load():
        ev = []
        for day, decision in FOMC_DECISIONS:
            ev.append({"kind": "fomc", "title": "Решение ФРС по ставке" if decision else
                       "ФРС: стратегическая сессия (без решения)",
                       "ts": ny_ts(day, "14:00"), "imp": 3 if decision else 1, "approx": False})
        missing = []
        for kind, (rid, hhmm, imp, title) in RELEASES.items():
            try:
                days = fred_release_dates(rid)
            except Exception:  # noqa: BLE001
                days = None
            if days is None:
                missing.append(kind)
                continue
            if kind == "claims":  # еженедельные: только ближайшие две
                fut = [d for d in days if d >= date.today().isoformat()][:2]
                past = [d for d in days if d < date.today().isoformat()][-1:]
                days = past + fut
            for d in days:
                ev.append({"kind": kind, "title": title, "ts": ny_ts(d, hhmm), "imp": imp, "approx": False})
        if "jobs" in missing:  # без ключа FRED: ориентировочно первая пятница месяца
            t = date.today()
            for i in range(-1, 4):
                y, m = (t.year + (t.month - 1 + i) // 12, (t.month - 1 + i) % 12 + 1)
                ev.append({"kind": "jobs", "title": RELEASES["jobs"][3], "approx": True,
                           "ts": ny_ts(first_friday(y, m).isoformat(), "08:30"), "imp": 3})
        t = date.today()
        for i in range(-1, 4):  # экспирации опционов на BTC (Deribit, 08:00 UTC в последнюю пятницу)
            y, m = (t.year + (t.month - 1 + i) // 12, (t.month - 1 + i) % 12 + 1)
            d = last_friday(y, m)
            q = m in (3, 6, 9, 12)
            ev.append({"kind": "expiry", "title": "Квартальная экспирация опционов BTC" if q else
                       "Экспирация опционов BTC", "imp": 2 if q else 1, "approx": False,
                       "ts": datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc).timestamp()})
        lo, hi = time.time() - 3 * 86400, time.time() + 120 * 86400  # храним только ближайшее окно
        ev = [e for e in ev if lo <= e["ts"] <= hi]
        return {"events": sorted(ev, key=lambda e: e["ts"]), "missing": missing}
    return cached("calendar", BG_CACHE_HOURS["calendar"], load)


BG_SOURCES = {
    "bg_labor": labor, "bg_rates": rates, "bg_dollar": dollar, "bg_credit": credit,
    "bg_ai": ai_companies, "bg_structure": structure, "bg_calendar": checkpoints,
}
