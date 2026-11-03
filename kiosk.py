"""Экран для старого iPad (iOS 6+) и любых слабых устройств.

Запуск:  python kiosk.py            → http://<IP-Mac>:8600 в локальной сети
         python kiosk.py --port 9000

Streamlit на iOS 6 не работает, поэтому здесь своя страница без внешних библиотек:
HTML собирается на сервере, а на устройстве работает только простой JavaScript (ES5).
Живую цену сервер сам берёт у Binance и отдаёт устройству по HTTP: старый iPad не сможет
подключиться к современным HTTPS-серверам бирж напрямую.
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

import requests

import storage
from config import GROUP_WEIGHTS, INTERVAL_SECONDS, USER_AGENT
from presentation import GROUPS, SIGNALS, NEUTRAL_TXT, ZONES, summary, checklist_rows, verdict

PRICE_EVERY = 3     # секунд между обновлениями цены
SPARK_EVERY = 60    # секунд между обновлениями графика за сутки
PAGE_RELOAD_MIN = 5  # как часто перезагружать страницу ради свежих данных монитора

# Цвета для тёмного экрана: ярче, чем на дашборде, чтобы читались на чёрном фоне
UP, DOWN, FLAT = "#3DBE9A", "#E5674F", "#8E8E93"


SHORT_TITLES = {"Приток в ETF несколько дней подряд": "Приток в ETF 3+ дня"}


def kcolor(v):
    return UP if v > 0.15 else DOWN if v < -0.15 else FLAT


# ---------------------------------------------------------------- живая цена
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


# ---------------------------------------------------------------- разметка
def bar(v, big=False):
    w = min(abs(v), 1) * 50
    c = kcolor(v)
    left = 50 if v >= 0 else 50 - w
    if w < 1:
        left, w = 49.6, 0.8
    return (f'<div class="track{" big" if big else ""}"><i class="axis"></i>'
            f'<i class="fill" style="left:{left:.1f}%;width:{w:.1f}%;background:{c}"></i></div>')


def gauge(value):
    def zc(lo, hi):
        return UP if lo >= 15 else DOWN if hi <= -15 else "#636366"
    zones = "".join(
        f'<i class="zone" style="left:{(lo + 100) / 2:.1f}%;width:{(hi - lo) / 2:.1f}%;'
        f'background:{zc(lo, hi)};opacity:{min(a * 2.2, .7):.2f}"></i>' for lo, hi, _, a in ZONES)
    return (f'<div class="gauge">{zones}'
            f'<i class="mark" style="left:{(value + 100) / 2:.1f}%"></i></div>'
            '<div class="scale"><span class="l">−100 продавцы</span><span class="c">0</span>'
            '<span class="r">покупатели +100</span></div>')


def md_bold(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(text))


def render():
    snap = storage.last(1)
    if not snap:
        return PAGE.replace("{{BODY}}", '<div class="empty">Данных пока нет.<br>Запустите monitor.py на Mac.</div>')
    snap = snap[0]
    sc, raw = snap["scored"], snap["raw"]
    prev = storage.last(1, before_ts=snap["ts"] - 86400)
    prev_sc = prev[0]["scored"] if prev else None
    age_min = (time.time() - snap["ts"]) / 60
    errors = [k for k, v in raw.items() if isinstance(v, dict) and "error" in v]

    # индекс
    comp = sc["composite"]
    day = ""
    if prev_sc:
        d = comp - prev_sc["composite"]
        day = f'<span style="color:{kcolor(d / 30)}">{"▲" if d > 0 else "▼" if d < 0 else "•"} {d:+.0f} за сутки</span>'
    lines = "".join(f"<p>{md_bold(x)}</p>" for x in summary(sc))
    index_html = f"""
      <div class="label">Индекс сигналов</div>
      <div class="idx"><span class="num" style="color:{kcolor(comp / 30)}">{comp:+.0f}</span>
        <span class="reg">{escape(sc['regime'])}<br><small>{day}</small></span></div>
      {gauge(comp)}
      <div class="sum">{lines}</div>"""

    # чек-лист
    rows = checklist_rows(raw)
    passed = sum(r[0] for r in rows)
    checks = "".join(
        f'<div class="chk {"on" if ok else ""}"><div class="in"><b>{"✓" if ok else "○"} '
        f'{escape(SHORT_TITLES.get(title, title))}</b>'
        f'<small>{escape(now)}</small></div></div>' for ok, title, now, need in rows)
    check_html = (f'<div class="label">Подтверждение импульса: <b>{passed} из 4</b> — {verdict(passed)}</div>'
                  f'<div class="row4">{checks}</div>')

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
                f'<span class="gd">{dd}</span></div>'
                + (bar(v, big=True) if v is not None else ""))
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
    groups_html = f'<div class="row4 groups">{"".join(cells)}</div>'

    # подвал
    dv = raw.get("dvol", {})
    foot = [f"Монитор: {datetime.fromtimestamp(snap['ts']):%H:%M}"]
    if age_min > 2.5 * INTERVAL_SECONDS / 60:
        foot.append(f'<span style="color:{DOWN}">данные устарели на {age_min:.0f} мин</span>')
    if "dvol" in dv:
        foot.append(f"DVOL {dv['dvol']:.0f}")
    if errors:
        foot.append(f'<span style="color:{DOWN}">ошибки: {", ".join(errors)}</span>')

    body = f"""
    <div class="top">
      <div class="card price" id="card">
        <div class="ph"><b>BTC</b><span class="sub">Bitcoin · USD</span>
          <span id="status" class="status">…</span></div>
        <div class="pr"><span id="price">—</span>
          <span id="pill" class="pill">—</span></div>
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
      <div class="card index">{index_html}</div>
    </div>
    <div class="card checks">{check_html}</div>
    {groups_html}
    <div class="foot"><span id="clock"></span> · {" · ".join(foot)}</div>"""
    return PAGE.replace("{{BODY}}", body)


PAGE = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="apple-mobile-web-app-title" content="BTC">
<title>BTC</title>
<style>
* {margin:0; padding:0; -webkit-box-sizing:border-box; box-sizing:border-box;}
html, body {background:#000; color:#f2f2f7; font-family:"Helvetica Neue", Helvetica, Arial, sans-serif;
  -webkit-text-size-adjust:none; -webkit-user-select:none; -webkit-tap-highlight-color:rgba(0,0,0,0);}
body {padding:12px 12px 6px;}
b {font-weight:500;}
small {font-size:12px; color:#8e8e93;}
.card {background:#1c1c1e; border-radius:12px; padding:12px 14px; margin-bottom:10px;}
.label {font-size:13px; color:#8e8e93; margin-bottom:6px;}
.label b {color:#f2f2f7;}
.empty {font-size:22px; text-align:center; padding-top:280px; color:#8e8e93; line-height:1.6;}

/* верхний ряд: цена + индекс */
.top {font-size:0; margin-bottom:10px;}
.top .card {display:inline-block; vertical-align:top; font-size:15px; margin:0; height:262px;}
.top .price {width:39%; margin-right:1%;}
.top .index {width:60%;}

.ph b {font-size:22px; font-weight:700; letter-spacing:.5px;}
.ph .sub {font-size:13px; color:#8e8e93; margin-left:8px;}
.status {float:right; font-size:12px; color:#8e8e93; margin-top:6px;}
.status.live:before {content:"● "; color:""" + UP + """;}
.status.dead:before {content:"● "; color:""" + DOWN + """;}
.pr {margin-top:2px; height:58px; position:relative;}
#price {font-size:48px; font-weight:300; letter-spacing:-1px; -webkit-transition:color .6s; transition:color .6s;}
#price.up {color:""" + UP + """; -webkit-transition:none;}
#price.down {color:""" + DOWN + """; -webkit-transition:none;}
.pill {position:absolute; right:0; top:12px; min-width:92px; text-align:center; padding:6px 10px;
  border-radius:6px; font-size:17px; font-weight:500; color:#fff; background:#555; cursor:pointer;}
#spark {display:block; width:100%; height:120px; margin-top:6px;}
.hl {font-size:13px; color:#8e8e93; margin-top:6px;}
.hl span {margin-right:18px;}
.hl b {color:#f2f2f7;}

.idx {height:66px; overflow:hidden;}
.idx .num {font-size:60px; font-weight:700; line-height:60px; float:left; margin-right:14px;}
.idx .reg {font-size:21px; display:block; padding-top:6px; line-height:1.25;}
.idx .reg small {font-size:14px;}
.gauge {position:relative; height:12px; border-radius:6px; overflow:hidden; background:#2c2c2e; margin-top:10px;}
.gauge .zone {position:absolute; top:0; bottom:0;}
.gauge .mark {position:absolute; top:0; bottom:0; width:4px; margin-left:-2px; background:#fff;}
.scale {position:relative; height:16px; font-size:11px; color:#8e8e93; margin-top:3px;}
.scale span {position:absolute;} .scale .l {left:0;} .scale .c {left:49%;} .scale .r {right:0;}
.sum {margin-top:10px; font-size:15px; line-height:1.4;}
.sum p {margin-bottom:6px;}
.sum b {color:#fff; font-weight:700;}

/* чек-лист и группы: четыре колонки */
.row4 {font-size:0; margin:0 -4px;}
.row4 > div {display:inline-block; vertical-align:top; width:25%; padding:0 4px; font-size:14px;}
.chk .in {border-radius:8px; padding:8px 10px; background:#2c2c2e; height:56px; overflow:hidden;}
.chk b {display:block; font-size:14px; color:#aeaeb2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.chk small {display:block; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.chk.on .in {background:#173a31;}
.chk.on b {color:#fff;}
.checks {padding-bottom:12px;}

.groups {margin-bottom:6px;}
.grp .in {background:#1c1c1e; border-radius:12px; padding:10px 12px 6px; height:322px; overflow:hidden;}
.grp.compact .sig small {display:none;}
.grp.compact .sig {margin-bottom:7px;}
.gh {height:24px; position:relative;}
.gh b {font-size:16px; font-weight:700;}
.gv {position:absolute; right:0; top:0; font-size:17px; font-weight:700;}
.gd {position:absolute; right:52px; top:3px; font-size:12px;}
.track {position:relative; height:6px; border-radius:3px; background:#2c2c2e; margin:3px 0 2px;}
.track.big {height:10px; border-radius:5px; margin:4px 0 8px;}
.track .axis {position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background:#636366;}
.track .fill {position:absolute; top:0; bottom:0; border-radius:3px;}
.sig {margin-bottom:5px;}
.sl {height:17px; position:relative; font-size:13px; color:#d1d1d6;}
.sl em {position:absolute; right:0; top:0; font-style:normal; font-weight:500;}
.sig small {display:block; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.foot {font-size:12px; color:#636366; text-align:center; padding-top:2px;}

/* портретная ориентация: всё в столбик, группы 2×2 */
@media (orientation: portrait) {
  .top .card {display:block; width:100% !important; margin:0 0 10px; height:auto;}
  #spark {height:96px;}
  .checks .row4 > div {width:50%; margin-bottom:8px;}
  .checks {padding-bottom:4px;}
  .grp .in {height:300px; padding:10px 10px 6px;}
  .sig small {display:none;}
  .sig {margin-bottom:7px;}
  .gh b {font-size:15px;}
  .gh small {display:none;}
  .gd {display:none;}
}
</style></head>
<body>{{BODY}}
<script>
(function () {
  var UP = '""" + UP + """', DOWN = '""" + DOWN + """';
  var mode = 'pct', last = null, shownPrice = null;

  function $(id) { return document.getElementById(id); }
  function fmt(n, d) {
    var p = n.toFixed(d).split('.');
    p[0] = p[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
    return p.join('.');
  }
  function sign(n) { return n > 0 ? '+' : n < 0 ? '−' : ''; }

  function get(url, ok, fail) {
    var r = new XMLHttpRequest(), done = false;
    var timer = setTimeout(function () { if (!done) { done = true; r.abort(); if (fail) fail(); } }, 8000);
    r.onreadystatechange = function () {
      if (r.readyState !== 4 || done) return;
      done = true; clearTimeout(timer);
      if (r.status === 200) ok(r.responseText); else if (fail) fail();
    };
    r.open('GET', url + '?t=' + new Date().getTime(), true);
    r.send(null);
  }

  function setStatus(live, text) {
    var s = $('status'); if (!s) return;
    s.className = 'status ' + (live ? 'live' : 'dead');
    s.innerHTML = text;
  }

  function drawPill() {
    if (!last) return;
    var p = $('pill'), up = last.chg >= 0;
    p.style.background = up ? UP : DOWN;
    p.innerHTML = mode === 'pct'
      ? sign(last.chg_pct) + Math.abs(last.chg_pct).toFixed(2) + '%'
      : sign(last.chg) + fmt(Math.abs(last.chg), 0);
  }

  function drawSpark(d) {
    var pts = d.spark, n = pts.length; if (n < 2) return;
    var min = Math.min.apply(null, pts.concat([d.open])), max = Math.max.apply(null, pts.concat([d.open]));
    var span = (max - min) || 1, W = 300, H = 90, pad = 4, line = [], i, x, y;
    for (i = 0; i < n; i++) {
      x = (i / (n - 1)) * W;
      y = pad + (1 - (pts[i] - min) / span) * (H - 2 * pad);
      line.push(x.toFixed(1) + ',' + y.toFixed(1));
    }
    var col = d.chg >= 0 ? UP : DOWN;
    $('line').setAttribute('points', line.join(' '));
    $('line').setAttribute('stroke', col);
    $('area').setAttribute('points', '0,' + H + ' ' + line.join(' ') + ' ' + W + ',' + H);
    $('g1').setAttribute('stop-color', col);
    $('g2').setAttribute('stop-color', col);
    var oy = (pad + (1 - (d.open - min) / span) * (H - 2 * pad)).toFixed(1);
    $('openl').setAttribute('y1', oy); $('openl').setAttribute('y2', oy);
  }

  function update(text) {
    var d; try { d = JSON.parse(text); } catch (e) { setStatus(false, 'ошибка данных'); return; }
    if (!d.ok) { setStatus(false, 'нет данных'); return; }
    var stale = d.server_ts - d.ts > 30;
    setStatus(!stale, stale ? 'цена устарела' : 'онлайн');
    var el = $('price');
    if (shownPrice !== null && d.price !== shownPrice) {
      el.className = d.price > shownPrice ? 'up' : 'down';
      setTimeout(function () { el.className = ''; }, 700);
    }
    shownPrice = d.price;
    el.innerHTML = '$' + fmt(d.price, 0);
    $('high').innerHTML = fmt(d.high, 0);
    $('low').innerHTML = fmt(d.low, 0);
    last = d; drawPill(); drawSpark(d);
  }

  function poll() {
    get('/api/price', update, function () { setStatus(false, 'нет связи с Mac'); });
  }

  function clock() {
    var c = $('clock'); if (!c) return;
    var t = new Date(), h = t.getHours(), m = t.getMinutes();
    c.innerHTML = (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
  }

  // Перезагрузка страницы ради свежих данных монитора — только если сервер доступен,
  // иначе Safari покажет страницу ошибки и перестанет обновляться.
  function softReload() {
    get('/healthz', function () { window.location.reload(); }, function () {});
  }

  if ($('pill')) {
    $('pill').onclick = function () { mode = mode === 'pct' ? 'abs' : 'pct'; drawPill(); };
    poll(); setInterval(poll, """ + str(PRICE_EVERY * 1000) + """);
  }
  clock(); setInterval(clock, 10000);
  setInterval(softReload, """ + str(PAGE_RELOAD_MIN * 60 * 1000) + """);
})();
</script>
</body></html>"""


# ---------------------------------------------------------------- сервер
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
        path = self.path.split("?")[0]
        try:
            if path == "/":
                self._send(200, render(), "text/html; charset=utf-8")
            elif path == "/api/price":
                self._send(200, json.dumps(FEED.get()), "application/json")
            elif path == "/healthz":
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
    print(f"Экран для iPad: http://{lan_ip()}:{args.port}  (Ctrl+C — остановить)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
