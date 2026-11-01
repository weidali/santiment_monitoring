"""Сборщики данных. Каждая функция возвращает dict и никогда не роняет программу:
при ошибке возвращает {"error": "..."}, чтобы остальные метрики продолжали работать."""
import io
import json
import os
import time

import pandas as pd
import requests

from config import BASE_DIR, HTTP_TIMEOUT, USER_AGENT, POLYMARKET_EVENT_SLUGS

S = requests.Session()
S.headers.update({"User-Agent": USER_AGENT})


def _get(url, **params):
    r = S.get(url, params=params or None, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def safe(fn):
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"[:300]}
    wrapper.__name__ = fn.__name__
    return wrapper


@safe
def price_and_trend():
    """Цена BTC, скользящие 50/200 дней и объём (Binance spot)."""
    kl = _get("https://api.binance.com/api/v3/klines",
              symbol="BTCUSDT", interval="1d", limit=210).json()
    close = pd.Series([float(k[4]) for k in kl])
    vol = pd.Series([float(k[7]) for k in kl])  # объём в USDT
    return {
        "price": close.iloc[-1],
        "ma50": close.tail(50).mean(),
        "ma200": close.tail(200).mean(),
        "chg_24h_pct": (close.iloc[-1] / close.iloc[-2] - 1) * 100,
        "chg_7d_pct": (close.iloc[-1] / close.iloc[-8] - 1) * 100,
        "vol_ratio": vol.iloc[-1] / vol.tail(30).mean(),  # объём сегодня к среднему за 30 дней
    }


@safe
def coinbase_premium():
    cb = float(_get("https://api.exchange.coinbase.com/products/BTC-USD/ticker").json()["price"])
    bn = float(_get("https://api.binance.com/api/v3/ticker/price", symbol="BTCUSDT").json()["price"])
    return {"coinbase": cb, "binance": bn, "premium_pct": (cb / bn - 1) * 100}


@safe
def derivatives():
    """Funding и open interest по бессрочным фьючерсам Binance."""
    prem = _get("https://fapi.binance.com/fapi/v1/premiumIndex", symbol="BTCUSDT").json()
    hist = _get("https://fapi.binance.com/futures/data/openInterestHist",
                symbol="BTCUSDT", period="1h", limit=25).json()
    oi_now = float(hist[-1]["sumOpenInterestValue"])
    oi_24h = float(hist[0]["sumOpenInterestValue"])
    return {
        "funding_pct": float(prem["lastFundingRate"]) * 100,
        "oi_usd": oi_now,
        "oi_chg_24h_pct": (oi_now / oi_24h - 1) * 100,
    }


@safe
def dvol():
    """Индекс подразумеваемой волатильности Deribit (DVOL): ожидаемая сила движения, без направления."""
    end = int(time.time() * 1000)
    start = end - 8 * 24 * 3600 * 1000
    d = _get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
             currency="BTC", start_timestamp=start, end_timestamp=end,
             resolution="3600").json()["result"]["data"]
    return {"dvol": d[-1][4], "dvol_7d_ago": d[0][4]}


@safe
def stablecoins():
    data = _get("https://stablecoins.llama.fi/stablecoincharts/all").json()
    caps = [float(x["totalCirculatingUSD"]["peggedUSD"]) for x in data[-31:]]
    return {
        "total_usd": caps[-1],
        "chg_7d_pct": (caps[-1] / caps[-8] - 1) * 100,
        "chg_30d_pct": (caps[-1] / caps[0] - 1) * 100,
    }


@safe
def etf_flows():
    """Потоки в спотовые BTC-ETF с Farside. Сайт иногда блокирует ботов —
    тогда можно вручную положить CSV в etf_flows_manual.csv (колонки: date,total_musd)."""
    try:
        html = _get("https://farside.co.uk/bitcoin-etf-flow-all-data/").text
        tables = pd.read_html(io.StringIO(html))
        df = max(tables, key=len)
        df.columns = [str(c[-1] if isinstance(c, tuple) else c) for c in df.columns]
        date_col, total_col = df.columns[0], df.columns[-1]
        df = df[pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).notna()]
        vals = (df[total_col].astype(str)
                .str.replace(",", "").str.replace("(", "-").str.replace(")", "")
                .replace({"-": "0", "": "0"}))
        flows = pd.to_numeric(vals, errors="coerce").dropna().tolist()
        dates = df[date_col].tolist()[-len(flows):]
    except Exception:
        manual = pd.read_csv(BASE_DIR / "etf_flows_manual.csv")
        flows, dates = manual["total_musd"].tolist(), manual["date"].tolist()
    last = flows[-5:]
    streak = 0
    for v in reversed(flows):
        if v > 0 and streak >= 0:
            streak += 1
        elif v < 0 and streak <= 0:
            streak -= 1
        else:
            break
    return {"last_day_musd": last[-1], "sum_3d_musd": sum(last[-3:]),
            "streak_days": streak, "last_date": str(dates[-1])}


def _yahoo_5d(ticker):
    r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
             range="1mo", interval="1d").json()
    closes = [c for c in r["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
    return closes[-1], closes[-6]


@safe
def macro():
    out = {}
    for name, t in {"dxy": "DX-Y.NYB", "us10y": "^TNX", "nasdaq": "^IXIC",
                    "vix": "^VIX", "oil": "CL=F", "usdjpy": "JPY=X"}.items():
        try:
            now, before = _yahoo_5d(t)
            out[name] = now
            if name == "us10y":
                out["us10y_5d_bp"] = (now - before) * 100
            else:
                out[f"{name}_5d_pct"] = (now / before - 1) * 100
        except Exception as e:  # noqa: BLE001
            out[f"{name}_error"] = str(e)[:120]
    return out


FRED_CACHE = BASE_DIR / ".fred_cache.json"
FRED_CACHE_HOURS = 6  # данные ФРС недельные, чаще опрашивать незачем


def _fred(series):
    """Ряд FRED. С ключом FRED_API_KEY — через официальный API (быстрее и стабильнее),
    без ключа — через публичный CSV. Берём только последние ~1,5 года, с повторами."""
    start = (pd.Timestamp.today() - pd.Timedelta(days=550)).strftime("%Y-%m-%d")
    key = os.environ.get("FRED_API_KEY")
    last_err = None
    for attempt in range(3):
        try:
            if key:
                r = S.get("https://api.stlouisfed.org/fred/series/observations",
                          params={"series_id": series, "api_key": key, "file_type": "json",
                                  "observation_start": start}, timeout=60)
                r.raise_for_status()
                obs = r.json()["observations"]
                df = pd.DataFrame(obs)[["date", "value"]].rename(columns={"value": "v"})
            else:
                r = S.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                          params={"id": series, "cosd": start}, timeout=60)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = ["date", "v"]
            df["v"] = pd.to_numeric(df["v"], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            return df.dropna().set_index("date")["v"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise last_err


def _read_fred_cache():
    try:
        return json.loads(FRED_CACHE.read_text())
    except Exception:  # noqa: BLE001
        return None


@safe
def net_liquidity():
    """Чистая ликвидность ФРС = баланс (WALCL) − счёт Казначейства (WTREGEN) − обратное РЕПО (RRPONTSYD).
    WALCL и WTREGEN в млн $, RRPONTSYD в млрд $. Результат кэшируется: при сбое FRED
    используется последнее успешное значение с пометкой stale."""
    cache = _read_fred_cache()
    if cache and time.time() - cache["fetched_at"] < FRED_CACHE_HOURS * 3600:
        return cache["data"]
    try:
        walcl = _fred("WALCL")
        tga = _fred("WTREGEN")
        rrp = _fred("RRPONTSYD").resample("W-WED").last() * 1000
        df = pd.concat([walcl, tga, rrp], axis=1, keys=["w", "t", "r"]).dropna()
        nl = (df["w"] - df["t"] - df["r"]) / 1e6  # трлн $
        data = {"net_liq_trn": float(nl.iloc[-1]),
                "chg_4w_pct": float((nl.iloc[-1] / nl.iloc[-5] - 1) * 100),
                "as_of": str(nl.index[-1].date())}
        FRED_CACHE.write_text(json.dumps({"fetched_at": time.time(), "data": data}))
        return data
    except Exception:
        if cache:  # FRED недоступен, но есть прошлое значение — оно всё ещё актуально (данные недельные)
            return {**cache["data"], "stale": True}
        raise


@safe
def polymarket():
    markets = []
    for slug in POLYMARKET_EVENT_SLUGS:
        ev = _get("https://gamma-api.polymarket.com/events", slug=slug).json()
        for e in ev:
            for m in e.get("markets", []):
                prices = json.loads(m.get("outcomePrices") or "[]")
                outcomes = json.loads(m.get("outcomes") or "[]")
                if prices and outcomes:
                    markets.append({"question": m.get("question"),
                                    "outcome": outcomes[0],
                                    "prob": float(prices[0]) * 100})
    return {"markets": markets}


ALL_SOURCES = {
    "trend": price_and_trend,
    "premium": coinbase_premium,
    "deriv": derivatives,
    "dvol": dvol,
    "stables": stablecoins,
    "etf": etf_flows,
    "macro": macro,
    "liquidity": net_liquidity,
    "polymarket": polymarket,
}


def collect_all():
    return {name: fn() for name, fn in ALL_SOURCES.items()}
