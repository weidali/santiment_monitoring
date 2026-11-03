"""Веб-интерфейс монитора. Работает в любом браузере, включая старый iPad на iOS 6.

Запуск:  python app.py                       → http://<IP-компьютера>:8600 в локальной сети
         python app.py --host 127.0.0.1      → только локально (так запускается на сервере)

Страницы:
  /         главный экран: живая цена, индекс, чек-лист, события, настроение, группы сигналов
  /details  подробности: история индекса и цены, все сигналы с фактическими значениями,
            Polymarket, статус источников данных

Здесь нет внешних библиотек на стороне браузера: HTML собирается на сервере, а на устройстве
работает только простой JavaScript (ES5). Живую цену сервер сам берёт у Binance и отдаёт
по HTTP: старые устройства не умеют подключаться к современным HTTPS-серверам бирж.
"""
import argparse
import json
import re
import socket
import threading
import time
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

import storage
from config import GROUP_WEIGHTS, INTERVAL_SECONDS, USER_AGENT
from presentation import (GROUPS, SIGNALS, NEUTRAL_TXT, ZONES, FNG_RU, summary, checklist_rows,
                          current_value, verdict, derive_events, dvol_mood)

PRICE_EVERY = 3        # секунд между обновлениями цены
SPARK_EVERY = 60       # секунд между обновлениями графика цены за сутки
PAGE_RELOAD_MIN = 5    # как часто главный экран подтягивает свежие данные монитора
DETAILS_IDLE_MIN = 3   # через сколько минут без касаний «Подробнее» возвращается на главный экран

UP, DOWN, FLAT = "#3DBE9A", "#E5674F", "#8E8E93"

SHORT_TITLES = {
    "Приток в ETF несколько дней подряд": "ETF: приток 3+ дня",
    "Американцы покупают": "Покупают в США",
    "Новые позиции без перегрева": "Позиции без перегрева",
}

SOURCE_NAMES = {
    "trend": "Цена и тренд · Binance", "premium": "Coinbase Premium", "deriv": "Деривативы · Binance",
    "dvol": "DVOL · Deribit", "stables": "Стейблкоины · DefiLlama", "etf": "ETF-потоки · Farside",
    "macro": "Макро · Yahoo", "liquidity": "Ликвидность ФРС · FRED", "polymarket": "Polymarket",
    "fng": "Страх и жадность · alternative.me",
}


def kcolor(v):
    return UP if v > 0.15 else DOWN if v < -0.15 else FLAT


def ev_time(ts):
    t = datetime.fromtimestamp(ts)
    return f"{t:%H:%M}" if t.date() == datetime.now().date() else f"{t:%H:%M}<br><small>{t:%d.%m}</small>"


def when(ts):
    t = datetime.fromtimestamp(ts)
    return f"{t:%H:%M}" if t.date() == datetime.now().date() else f"{t:%d.%m %H:%M}"


# ================================================================ живая цена
class PriceFeed:
    def __init__(self):
        self.data = {"ok": False}
        self.lock = threading.Lock()
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._spark, self._spark_ts = [], 0

    def _tick(self):
        t = self.s.get("https://api.binance.com/api/v3/ticker/24hr",
                       params={"symbol": "BTCUSDT"}, timeout=8).json()
        if time.time() - self._spark_ts > SPARK_EVERY:
            kl = self.s.get("https://api.binance.com/api/v3/klines",
                            params={"symbol": "BTCUSDT", "interval": "15m", "limit": 96}, timeout=8).json()
            self._spark = [round(float(k[4]), 2) for k in kl]
            self._spark_ts = time.time()
        price = float(t["lastPrice"])
        spark = self._spark[:-1] + [price] if self._spark else [price]
        return {"ok": True, "ts": time.time(), "price": price,
                "open": float(t["openPrice"]), "chg": float(t["priceChange"]),
                "chg_pct": float(t["priceChangePercent"]),
                "high": float(t["highPrice"]), "low": float(t["lowPrice"]), "spark": spark}

    def run(self):
        while True:
            try:
                d = self._tick()
                with self.lock:
                    self.data = d
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.data = {**self.data, "error": str(e)[:200]}
            time.sleep(PRICE_EVERY)

    def get(self):
        with self.lock:
            d = dict(self.data)
        d["server_ts"] = time.time()
        return d


FEED = PriceFeed()


# ================================================================ общие элементы
def bar(v, big=False):
    w = min(abs(v), 1) * 50
    left = 50 if v >= 0 else 50 - w
    if w < 1:
        left, w = 49.6, 0.8
    return (f'<div class="track{" big" if big else ""}"><i class="axis"></i>'
            f'<i class="fill" style="left:{left:.1f}%;width:{w:.1f}%;background:{kcolor(v)}"></i></div>')


def zone_color(lo, hi):
    return UP if lo >= 15 else DOWN if hi <= -15 else "#636366"


def gauge(value):
    zones = "".join(
        f'<i class="zone" style="left:{(lo + 100) / 2:.1f}%;width:{(hi - lo) / 2:.1f}%;'
        f'background:{zone_color(lo, hi)};opacity:{min(a * 2.2, .7):.2f}"></i>' for lo, hi, _, a in ZONES)
    return (f'<div class="gauge">{zones}<i class="mark" style="left:{(value + 100) / 2:.1f}%"></i></div>'
            '<div class="scale"><span class="l">−100 продавцы</span><span class="c">0</span>'
            '<span class="r">покупатели +100</span></div>')


def md_bold(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(text))


def arrow(d, fmt="{:+.2f}"):
    if abs(d) < 0.005:
        return ""
    return f'<span style="color:{kcolor(d * 3)}">{"▲" if d > 0 else "▼"} {fmt.format(d)}</span>'


def load():
    snap = storage.last(1)
    if not snap:
        return None
    snap = snap[0]
    prev = storage.last(1, before_ts=snap["ts"] - 86400)
    return snap, (prev[0]["scored"] if prev else None)


def page(title, css, body, script):
    return (PAGE_TEMPLATE.replace("{{TITLE}}", title).replace("{{CSS}}", BASE_CSS + css)
            .replace("{{BODY}}", body).replace("{{SCRIPT}}", COMMON_JS + script)
            .replace("%UP%", UP).replace("%DOWN%", DOWN))


EMPTY = '<div class="empty">Данных пока нет.<br>Запустите monitor.py.</div>'


# ================================================================ главный экран
def render_main():
    loaded = load()
    if not loaded:
        return page("BTC", MAIN_CSS, EMPTY, "")
    snap, prev_sc = loaded
    sc, raw = snap["scored"], snap["raw"]
    age_min = (time.time() - snap["ts"]) / 60
    errors = [k for k, v in raw.items() if isinstance(v, dict) and "error" in v]

    # индекс
    comp = sc["composite"]
    day = arrow(comp - prev_sc["composite"], "{:+.0f}") + " за сутки" if prev_sc else ""
    if prev_sc and abs(comp - prev_sc["composite"]) < 0.5:
        day = "без изменений за сутки"
    lines = "".join(f"<p>{md_bold(x)}</p>" for x in summary(sc))
    index_html = f"""
      <div class="label">Индекс сигналов <span class="more">Подробнее ›</span></div>
      <div class="idx"><span class="num" style="color:{kcolor(comp / 30)}">{comp:+.0f}</span>
        <span class="reg">{escape(sc['regime'])}<br><small>{day}</small></span></div>
      {gauge(comp)}
      <div class="sum">{lines}</div>"""

    # события
    hist = storage.history(7)
    events = derive_events(hist, limit=5)
    if events:
        ev_html = "".join(
            f'<div class="ev"><span class="t">{ev_time(ts)}</span>'
            f'<i class="d" style="background:{UP if d > 0 else DOWN if d < 0 else FLAT}"></i>'
            f'<span class="x">{escape(text)}</span></div>' for ts, d, text in events)
    else:
        ev_html = ('<div class="noev">За неделю ничего заметного. Здесь появятся смены режима, '
                   'подтверждение импульса, перегрев плеча и крупные дни по ETF.</div>')

    # чек-лист
    rows = checklist_rows(raw)
    passed = sum(r[0] for r in rows)
    checks = "".join(
        f'<div class="chk {"on" if ok else ""}"><div class="in"><b>{"✓" if ok else "○"} '
        f'{escape(SHORT_TITLES.get(title, title))}</b><small>{escape(now)}</small></div></div>'
        for ok, title, now, need in rows)

    # настроение
    fng, dv = raw.get("fng", {}), raw.get("dvol", {})
    mood = []
    if "value" in fng:
        fv = fng["value"]
        fcol = kcolor((fv - 50) / 25)
        fd = f" {arrow(fv - fng['prev'], '{:+.0f}')}" if fng.get("prev") is not None else ""
        mood.append(f'<div class="m"><span class="mv" style="color:{fcol}">{fv}</span>'
                    f'<span class="ml">Страх и жадность<br><small>{FNG_RU.get(fng["label"], fng["label"])}{fd}'
                    f'</small></span></div>')
    if "dvol" in dv:
        mood.append(f'<div class="m"><span class="mv">{dv["dvol"]:.0f}</span>'
                    f'<span class="ml">Волатильность<br><small>ожидания: {dvol_mood(dv["dvol"])}</small>'
                    f'</span></div>')
    mood_html = "".join(mood) or '<small>нет данных</small>'

    # группы
    cells = []
    for g, (gname, _) in GROUPS.items():
        v = sc["groups"].get(g)
        dd = ""
        if v is not None and prev_sc and prev_sc["groups"].get(g) is not None:
            x = v - prev_sc["groups"][g]
            if abs(x) >= 0.01:
                dd = f'<span style="color:{kcolor(x * 3)}">{"▲" if x > 0 else "▼"}{abs(x):.2f}</span>'
        head = (f'<div class="gh"><b>{gname}</b> <small>{GROUP_WEIGHTS[g]:.0%}</small>'
                f'<span class="gv" style="color:{kcolor(v or 0)}">{"—" if v is None else f"{v:+.2f}"}</span>'
                f'<span class="gd">{dd}</span></div>' + (bar(v, big=True) if v is not None else ""))
        sigs = []
        for k, (label, pos, neg) in SIGNALS.items():
            s = sc["signals"].get(k)
            if not s or s["group"] != g or s["value"] is None:
                continue
            sv = s["value"]
            meaning = pos if sv > 0.15 else neg if sv < -0.15 else NEUTRAL_TXT.get(k, "нейтрально")
            sigs.append(f'<div class="sig"><div class="sl"><span>{escape(label)}</span>'
                        f'<em style="color:{kcolor(sv)}">{sv:+.1f}</em></div>{bar(sv)}'
                        f'<small>{escape(meaning)}</small></div>')
        cls = "grp compact" if len(sigs) > 4 else "grp"
        cells.append(f'<div class="{cls}"><div class="in">{head}{"".join(sigs)}</div></div>')

    foot = [f"Монитор: {when(snap['ts'])}"]
    if age_min > 2.5 * INTERVAL_SECONDS / 60:
        foot.append(f'<span style="color:{DOWN}">данные устарели на {age_min:.0f} мин</span>')
    if errors:
        foot.append(f'<span style="color:{DOWN}">ошибки: {", ".join(errors)}</span>')
    foot.append('<span class="link" data-go="/details">Подробнее ›</span>')

    body = f"""
    <div class="row top">
      <div class="card price">
        <div class="ph"><b>BTC</b><span class="sub">Bitcoin · USD</span>
          <span id="status" class="status">…</span></div>
        <div class="pr"><span id="price">—</span><span id="pill" class="pill">—</span></div>
        <svg id="spark" viewBox="0 0 300 90" preserveAspectRatio="none">
          <defs><linearGradient id="gr" x1="0" y1="0" x2="0" y2="1">
            <stop id="g1" offset="0" stop-color="{UP}" stop-opacity="0.35"/>
            <stop id="g2" offset="1" stop-color="{UP}" stop-opacity="0"/></linearGradient></defs>
          <polygon id="area" points="" fill="url(#gr)"/>
          <line id="openl" x1="0" x2="300" y1="45" y2="45" stroke="#666" stroke-width="1" stroke-dasharray="3,3"/>
          <polyline id="line" points="" fill="none" stroke="{UP}" stroke-width="2"/>
        </svg>
        <div class="hl"><span>24ч макс <b id="high">—</b></span><span>мин <b id="low">—</b></span></div>
      </div>
      <div class="card index" data-go="/details">{index_html}</div>
      <div class="card events"><div class="label">Что происходило</div>{ev_html}</div>
    </div>
    <div class="row mid">
      <div class="card checks"><div class="label">Подтверждение импульса: <b>{passed} из 4</b> — {verdict(passed)}</div>
        <div class="row4">{checks}</div></div>
      <div class="card mood">{mood_html}</div>
    </div>
    <div class="row4 groups">{"".join(cells)}</div>
    <div class="foot"><span id="clock"></span> · {" · ".join(foot)}</div>"""
    return page("BTC", MAIN_CSS, body, MAIN_JS)


# ================================================================ экран «Подробнее»
def history_svg(hist):
    if len(hist) < 2:
        return '<div class="noev">История появится после нескольких запусков монитора.</div>'
    step = max(1, len(hist) // 400)
    pts = hist[::step] + ([hist[-1]] if (len(hist) - 1) % step else [])
    t0, t1 = pts[0]["ts"], pts[-1]["ts"]
    span_t = (t1 - t0) or 1
    W, H, L, R, T, B = 1000, 300, 44, 70, 10, 26
    pw, ph = W - L - R, H - T - B

    def x(ts):
        return L + (ts - t0) / span_t * pw

    def yi(v):
        return T + (1 - (v + 100) / 200) * ph

    prices = [p["raw"].get("trend", {}).get("price") for p in pts]
    pv = [p for p in prices if p]
    pmin, pmax = (min(pv), max(pv)) if pv else (0, 1)
    pspan = (pmax - pmin) or 1

    def yp(v):
        return T + 6 + (1 - (v - pmin) / pspan) * (ph - 12)

    out = [f'<svg class="hist" viewBox="0 0 {W} {H}" preserveAspectRatio="none">']
    for lo, hi, _, a in ZONES:
        out.append(f'<rect x="{L}" y="{yi(hi):.1f}" width="{pw}" height="{yi(lo) - yi(hi):.1f}" '
                   f'fill="{zone_color(lo, hi)}" fill-opacity="{min(a * 1.1, .3):.2f}"/>')
    for v in (-100, -40, 0, 40, 100):
        out.append(f'<text x="{L - 6}" y="{yi(v) + 4:.1f}" text-anchor="end" class="ax">{v:+d}</text>')
    for k in range(4):
        pvv = pmin + pspan * k / 3
        out.append(f'<text x="{W - R + 6}" y="{yp(pvv) + 4:.1f}" class="ax">{pvv / 1000:.1f}k</text>')
    days = int(span_t // 86400) or 1
    for k in range(min(days, 7) + 1):
        ts = t0 + span_t * k / min(days, 7)
        out.append(f'<text x="{x(ts):.1f}" y="{H - 6}" text-anchor="middle" class="ax">'
                   f'{datetime.fromtimestamp(ts):%d.%m}</text>')
    pline = " ".join(f"{x(p['ts']):.1f},{yp(pr):.1f}" for p, pr in zip(pts, prices) if pr)
    iline = " ".join(f"{x(p['ts']):.1f},{yi(p['composite']):.1f}" for p in pts)
    out.append(f'<polyline points="{pline}" fill="none" stroke="#aeaeb2" stroke-width="1.5" stroke-dasharray="4,3"/>')
    out.append(f'<polyline points="{iline}" fill="none" stroke="#5E9BFF" stroke-width="2.5"/>')
    out.append("</svg>")
    return "".join(out)


def render_details(days):
    loaded = load()
    if not loaded:
        return page("BTC · подробнее", DETAILS_CSS, EMPTY, DETAILS_JS)
    snap, prev_sc = loaded
    sc, raw = snap["scored"], snap["raw"]

    tabs = "".join(f'<span class="tab{" on" if d == days else ""}" data-go="/details?days={d}">{d} дн.</span>'
                   for d in (1, 7, 30))
    hist_html = history_svg(storage.history(days))

    rows = []
    for g, (gname, hint) in GROUPS.items():
        v = sc["groups"].get(g)
        dd = ""
        if v is not None and prev_sc and prev_sc["groups"].get(g) is not None:
            dd = arrow(v - prev_sc["groups"][g]) + " за сутки" if abs(v - prev_sc["groups"][g]) >= .01 else ""
        rows.append(f'<tr class="g"><td><b>{gname}</b> <small>вес {GROUP_WEIGHTS[g]:.0%}</small></td>'
                    f'<td>{dd}</td><td>{bar(v, True) if v is not None else "—"}</td>'
                    f'<td class="n" style="color:{kcolor(v or 0)}">{"—" if v is None else f"{v:+.2f}"}</td>'
                    f'<td><small>{escape(hint)}</small></td></tr>')
        for k, (label, pos, neg) in SIGNALS.items():
            s = sc["signals"].get(k)
            if not s or s["group"] != g or s["value"] is None:
                continue
            sv = s["value"]
            meaning = (s["comment"][:1].lower() + s["comment"][1:]) if k == "leverage" else \
                pos if sv > 0.15 else neg if sv < -0.15 else NEUTRAL_TXT.get(k, "нейтрально")
            rows.append(f'<tr><td class="s">{escape(label)}</td><td>{escape(current_value(k, raw))}</td>'
                        f'<td>{bar(sv)}</td><td class="n" style="color:{kcolor(sv)}">{sv:+.2f}</td>'
                        f'<td>{escape(meaning)}</td></tr>')
    table = ('<table class="bd"><tr class="h"><th>Сигнал</th><th>Сейчас</th><th>Оценка</th><th></th>'
             '<th>Что это значит</th></tr>' + "".join(rows) + "</table>")

    pm = raw.get("polymarket", {}).get("markets", [])
    pm_html = "".join(f'<div class="pm"><span>{escape(m["question"])}</span><b>{m["prob"]:.0f}%</b></div>'
                      for m in pm) or '<small>Рынки не выбраны: добавьте slug в POLYMARKET_EVENT_SLUGS в config.py.</small>'

    src = []
    for k, label in SOURCE_NAMES.items():
        v = raw.get(k, {})
        if isinstance(v, dict) and "error" in v:
            st, col, note = "ошибка", DOWN, v["error"]
        elif isinstance(v, dict) and v.get("stale"):
            st, col, note = "из кэша", FLAT, f"данные на {v.get('as_of', '?')}"
        elif not v:
            st, col, note = "нет", FLAT, "источник ещё не опрашивался"
        else:
            st, col, note = "работает", UP, v.get("last_date") or v.get("as_of") or ""
        src.append(f'<tr><td>{escape(label)}</td><td style="color:{col}">● {st}</td>'
                   f'<td class="note">{escape(str(note))}</td></tr>')

    body = f"""
    <div class="bar"><span class="back" data-go="/">‹ Назад</span>
      <b>Подробности</b><span class="upd">замер {when(snap['ts'])} · <span id="clock"></span></span></div>
    <div class="card"><div class="label">Индекс <i class="sw" style="background:#5E9BFF"></i> и цена
      <i class="sw dash"></i><span class="tabs">{tabs}</span></div>{hist_html}
      <small>Интереснее всего расхождения: цена растёт, а индекс падает — рост теряет опору;
      цена стоит, а индекс растёт — под рынок заходят деньги.</small></div>
    <div class="card"><div class="label">Все сигналы</div>{table}</div>
    <div class="row2">
      <div><div class="card"><div class="label">Рынки предсказаний · Polymarket</div>{pm_html}</div></div>
      <div><div class="card"><div class="label">Источники данных</div><table class="src">{"".join(src)}</table></div></div>
    </div>"""
    return page("BTC · подробнее", DETAILS_CSS, body, DETAILS_JS)


# ================================================================ стили и скрипты
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="apple-mobile-web-app-title" content="BTC">
<title>{{TITLE}}</title>
<style>{{CSS}}</style></head>
<body>{{BODY}}
<script>{{SCRIPT}}</script>
</body></html>"""

# Только то, что умеет Safari на iOS 6: без flex, grid и CSS-переменных
BASE_CSS = """
* {margin:0; padding:0; -webkit-box-sizing:border-box; box-sizing:border-box;}
html, body {background:#000; color:#f2f2f7; font-family:"Helvetica Neue", Helvetica, Arial, sans-serif;
  -webkit-text-size-adjust:none; -webkit-tap-highlight-color:rgba(0,0,0,0);}
body {padding:12px 12px 6px;}
b {font-weight:500;}
small {font-size:12px; color:#8e8e93;}
.card {background:#1c1c1e; border-radius:12px; padding:12px 14px; margin-bottom:10px;}
.label {font-size:13px; color:#8e8e93; margin-bottom:6px;}
.label b {color:#f2f2f7;}
.empty {font-size:22px; text-align:center; padding-top:280px; color:#8e8e93; line-height:1.6;}
[data-go] {cursor:pointer;}
.track {position:relative; height:6px; border-radius:3px; background:#2c2c2e; margin:3px 0 2px;}
.track.big {height:10px; border-radius:5px; margin:4px 0 8px;}
.track .axis {position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background:#636366;}
.track .fill {position:absolute; top:0; bottom:0; border-radius:3px;}
.noev {font-size:13px; color:#8e8e93; line-height:1.45;}
"""

MAIN_CSS = """
body {-webkit-user-select:none;}
.row {font-size:0; margin-bottom:10px;}
.row > .card {display:inline-block; vertical-align:top; font-size:15px; margin:0;}
.top > .card {height:262px; overflow:hidden;}
.top .price {width:34%; margin-right:1%;}
.top .index {width:38%; margin-right:1%;}
.top .events {width:26%;}
.mid > .card {height:100px;}
.mid .checks {width:74%; margin-right:1%;}
.mid .mood {width:25%;}

.ph b {font-size:22px; font-weight:700; letter-spacing:.5px;}
.ph .sub {font-size:13px; color:#8e8e93; margin-left:8px;}
.status {float:right; font-size:12px; color:#8e8e93; margin-top:6px;}
.status.live:before {content:"● "; color:%UP%;}
.status.dead:before {content:"● "; color:%DOWN%;}
.pr {margin-top:2px; height:58px; position:relative;}
#price {font-size:44px; font-weight:300; letter-spacing:-1px; -webkit-transition:color .6s; transition:color .6s;}
#price.up {color:%UP%; -webkit-transition:none;}
#price.down {color:%DOWN%; -webkit-transition:none;}
.pill {position:absolute; right:0; top:10px; min-width:84px; text-align:center; padding:6px 8px;
  border-radius:6px; font-size:16px; font-weight:500; color:#fff; background:#555; cursor:pointer;}
#spark {display:block; width:100%; height:120px; margin-top:6px;}
.hl {font-size:13px; color:#8e8e93; margin-top:6px;}
.hl span {margin-right:16px;}
.hl b {color:#f2f2f7;}

.more {float:right; color:#5E9BFF;}
.idx {height:62px; overflow:hidden;}
.idx .num {font-size:54px; font-weight:700; line-height:56px; float:left; margin-right:12px;}
.idx .reg {font-size:19px; display:block; padding-top:5px; line-height:1.25;}
.idx .reg small {font-size:13px;}
.gauge {position:relative; height:12px; border-radius:6px; overflow:hidden; background:#2c2c2e; margin-top:8px;}
.gauge .zone {position:absolute; top:0; bottom:0;}
.gauge .mark {position:absolute; top:0; bottom:0; width:4px; margin-left:-2px; background:#fff;}
.scale {position:relative; height:16px; font-size:11px; color:#8e8e93; margin-top:3px;}
.scale span {position:absolute;} .scale .l {left:0;} .scale .c {left:49%;} .scale .r {right:0;}
.sum {margin-top:8px; font-size:14px; line-height:1.38;}
.sum p {margin-bottom:5px;}
.sum b {color:#fff; font-weight:700;}

.ev {position:relative; padding:6px 0 6px 52px; font-size:13px; line-height:1.3;
  border-bottom:1px solid #2c2c2e; min-height:40px;}
.ev:last-child {border-bottom:none;}
.ev .t {position:absolute; left:0; top:6px; color:#aeaeb2; font-size:12px; line-height:1.3; width:38px;}
.ev .t small {font-size:11px; color:#8e8e93;}
.ev .d {position:absolute; left:40px; top:11px; width:6px; height:6px; border-radius:3px;}
.ev .x {display:block; max-height:34px; overflow:hidden;}

.row4 {font-size:0; margin:0 -4px;}
.row4 > div {display:inline-block; vertical-align:top; width:25%; padding:0 4px; font-size:14px;}
.chk .in {border-radius:8px; padding:8px 8px; background:#2c2c2e; height:56px; overflow:hidden;}
.chk b {display:block; font-size:13px; color:#aeaeb2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.chk small {display:block; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.chk.on .in {background:#173a31;}
.chk.on b {color:#fff;}

.m {height:36px; margin-bottom:4px; overflow:hidden;}
.m .mv {float:left; width:48px; font-size:28px; font-weight:700; line-height:34px;}
.m .ml {display:block; font-size:13px; line-height:17px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.m .ml small {font-size:12px;}

.groups {margin-bottom:6px;}
.grp .in {background:#1c1c1e; border-radius:12px; padding:10px 12px 6px; height:306px; overflow:hidden;}
.grp.compact .sig small {display:none;}
.grp.compact .sig {margin-bottom:6px;}
.gh {height:24px; position:relative;}
.gh b {font-size:16px; font-weight:700;}
.gv {position:absolute; right:0; top:0; font-size:17px; font-weight:700;}
.gd {position:absolute; right:52px; top:3px; font-size:12px;}
.sig {margin-bottom:5px;}
.sl {height:17px; position:relative; font-size:13px; color:#d1d1d6;}
.sl em {position:absolute; right:0; top:0; font-style:normal; font-weight:500;}
.sig small {display:block; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.foot {font-size:12px; color:#636366; text-align:center; padding-top:2px;}
.foot .link {color:#5E9BFF;}

@media (orientation: portrait) {
  .top .price {width:49.5%;}
  .top .index {width:49.5%; margin-right:0;}
  .top .events {display:block; width:100%; height:auto; margin-top:10px;}
  .events .ev {display:inline-block; vertical-align:top; width:50%; border-bottom:none; padding-right:10px;}
  .events .ev:nth-child(n+6) {display:none;}
  .events {height:auto !important;}
  .mid > .card {height:130px;}
  .checks .row4 > div {width:50%; margin-bottom:6px;}
  .checks .chk .in {height:46px; padding:6px 8px;}
  .chk small {margin-top:1px;}
  .grp .in {height:340px; padding:10px 10px 6px;}
  .grp .sig {margin-bottom:9px;}
  .sig small {display:none;}
  .sig {margin-bottom:6px;}
  .gh small, .gd {display:none;}
}
"""

DETAILS_CSS = """
.bar {height:40px; position:relative; text-align:center; font-size:17px; line-height:40px; margin-bottom:6px;}
.bar .back {position:absolute; left:0; top:0; color:#5E9BFF; font-size:17px;}
.bar .upd {position:absolute; right:0; top:0; font-size:12px; color:#8e8e93;}
.tabs {float:right;}
.tab {display:inline-block; padding:4px 12px; margin-left:4px; border-radius:6px; background:#2c2c2e;
  color:#d1d1d6; font-size:13px;}
.tab.on {background:#5E9BFF; color:#fff;}
.sw {display:inline-block; width:14px; height:3px; vertical-align:middle; margin:0 4px 0 2px;}
.sw.dash {border-top:2px dashed #aeaeb2; height:0;}
svg.hist {display:block; width:100%; height:300px; margin:6px 0 8px;}
svg.hist .ax {font-size:13px; fill:#8e8e93;}
table {width:100%; border-collapse:collapse; font-size:14px;}
table.bd th {text-align:left; font-weight:500; font-size:12px; color:#8e8e93; padding:6px 8px;
  border-bottom:1px solid #3a3a3c;}
table.bd tr.g td:first-child {white-space:nowrap;}
table.bd td {padding:6px 8px; border-bottom:1px solid #2c2c2e; vertical-align:middle;}
table.bd td.s {padding-left:22px; color:#d1d1d6;}
table.bd td.n {width:56px; text-align:right; font-weight:500;}
table.bd tr.g td {background:#242426; padding-top:9px; padding-bottom:9px;}
table.bd tr.g b {font-size:15px; font-weight:700;}
table.bd td .track {width:150px;}
.row2 {font-size:0; margin:0 -5px;}
.row2 > div {display:inline-block; vertical-align:top; width:50%; padding:0 5px; font-size:14px;}
.pm {position:relative; padding:7px 50px 7px 0; border-bottom:1px solid #2c2c2e; font-size:14px;}
.pm b {position:absolute; right:0; top:7px; font-weight:700;}
table.src td {padding:6px 4px; border-bottom:1px solid #2c2c2e; font-size:13px;}
table.src td.note {color:#8e8e93; font-size:12px; word-break:break-all;}
@media (orientation: portrait) { .row2 > div {width:100%;} }
"""

# ES5: без let/const, стрелочных функций, fetch и шаблонных строк
COMMON_JS = """
function $(id) { return document.getElementById(id); }
function get(url, ok, fail) {
  var r = new XMLHttpRequest(), done = false;
  var timer = setTimeout(function () { if (!done) { done = true; r.abort(); if (fail) fail(); } }, 8000);
  r.onreadystatechange = function () {
    if (r.readyState !== 4 || done) return;
    done = true; clearTimeout(timer);
    if (r.status === 200) ok(r.responseText); else if (fail) fail();
  };
  r.open('GET', url + (url.indexOf('?') < 0 ? '?' : '&') + 't=' + new Date().getTime(), true);
  r.send(null);
}
// Переход только если сервер отвечает: иначе Safari застрянет на странице ошибки.
// location вместо ссылок: в режиме «на экране Домой» обычные ссылки открываются в Safari.
function go(url) { get('/healthz', function () { window.location.href = url; }, function () {}); }
(function () {
  var all = document.getElementsByTagName('*'), i;
  for (i = 0; i < all.length; i++) {
    if (all[i].getAttribute('data-go')) {
      all[i].onclick = (function (u) { return function () { go(u); }; })(all[i].getAttribute('data-go'));
    }
  }
  function clock() {
    var c = $('clock'); if (!c) return;
    var t = new Date(), h = t.getHours(), m = t.getMinutes();
    c.innerHTML = (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
  }
  clock(); setInterval(clock, 10000);
})();
"""

MAIN_JS = """
(function () {
  var UP = '%UP%', DOWN = '%DOWN%';
  var mode = 'pct', last = null, shown = null;
  function fmt(n, d) {
    var p = n.toFixed(d).split('.');
    p[0] = p[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
    return p.join('.');
  }
  function sign(n) { return n > 0 ? '+' : n < 0 ? '−' : ''; }
  function status(live, text) {
    var s = $('status'); if (!s) return;
    s.className = 'status ' + (live ? 'live' : 'dead'); s.innerHTML = text;
  }
  function pill() {
    if (!last) return;
    var p = $('pill');
    p.style.background = last.chg >= 0 ? UP : DOWN;
    p.innerHTML = mode === 'pct' ? sign(last.chg_pct) + Math.abs(last.chg_pct).toFixed(2) + '%'
                                 : sign(last.chg) + fmt(Math.abs(last.chg), 0);
  }
  function spark(d) {
    var pts = d.spark, n = pts.length; if (n < 2) return;
    var all = pts.concat([d.open]);
    var min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    var span = (max - min) || 1, W = 300, H = 90, pad = 4, line = [], i, x, y;
    for (i = 0; i < n; i++) {
      x = (i / (n - 1)) * W; y = pad + (1 - (pts[i] - min) / span) * (H - 2 * pad);
      line.push(x.toFixed(1) + ',' + y.toFixed(1));
    }
    var col = d.chg >= 0 ? UP : DOWN;
    $('line').setAttribute('points', line.join(' ')); $('line').setAttribute('stroke', col);
    $('area').setAttribute('points', '0,' + H + ' ' + line.join(' ') + ' ' + W + ',' + H);
    $('g1').setAttribute('stop-color', col); $('g2').setAttribute('stop-color', col);
    var oy = (pad + (1 - (d.open - min) / span) * (H - 2 * pad)).toFixed(1);
    $('openl').setAttribute('y1', oy); $('openl').setAttribute('y2', oy);
  }
  function update(text) {
    var d; try { d = JSON.parse(text); } catch (e) { status(false, 'ошибка данных'); return; }
    if (!d.ok) { status(false, 'нет данных'); return; }
    var stale = d.server_ts - d.ts > 30;
    status(!stale, stale ? 'цена устарела' : 'онлайн');
    var el = $('price');
    if (shown !== null && d.price !== shown) {
      el.className = d.price > shown ? 'up' : 'down';
      setTimeout(function () { el.className = ''; }, 700);
    }
    shown = d.price;
    el.innerHTML = '$' + fmt(d.price, 0);
    $('high').innerHTML = fmt(d.high, 0); $('low').innerHTML = fmt(d.low, 0);
    last = d; pill(); spark(d);
  }
  function poll() { get('/api/price', update, function () { status(false, 'нет связи'); }); }
  if ($('pill')) {
    $('pill').onclick = function (e) { mode = mode === 'pct' ? 'abs' : 'pct'; pill(); };
    poll(); setInterval(poll, %PRICE_MS%);
  }
  setInterval(function () { go('/'); }, %RELOAD_MS%);
})();
""".replace("%PRICE_MS%", str(PRICE_EVERY * 1000)).replace("%RELOAD_MS%", str(PAGE_RELOAD_MIN * 60000))

DETAILS_JS = """
(function () {
  var idle = 0;
  document.ontouchstart = function () { idle = 0; };
  document.onmousemove = function () { idle = 0; };
  setInterval(function () { idle += 1; if (idle >= %IDLE%) go('/'); }, 60000);
})();
""".replace("%IDLE%", str(DETAILS_IDLE_MIN))


# ================================================================ сервер
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path == "/":
                self._send(200, render_main(), "text/html; charset=utf-8")
            elif u.path == "/details":
                try:
                    days = int(parse_qs(u.query).get("days", ["7"])[0])
                except ValueError:
                    days = 7
                self._send(200, render_details(days if days in (1, 7, 30) else 7), "text/html; charset=utf-8")
            elif u.path == "/api/price":
                self._send(200, json.dumps(FEED.get()), "application/json")
            elif u.path == "/healthz":
                self._send(200, "ok", "text/plain")
            else:
                self._send(404, "not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            self._send(500, f"error: {escape(str(e))}", "text/plain; charset=utf-8")

    def log_message(self, *args):
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    threading.Thread(target=FEED.run, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = lan_ip() if args.host == "0.0.0.0" else args.host
    print(f"Монитор: http://{shown}:{args.port}  (Ctrl+C — остановить)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
