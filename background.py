"""Оценка шести фоновых маркеров: норма / внимание / стресс.

Каждый маркер собирается из нескольких подсигналов от −1 до +1 (как в быстром индексе), их среднее
даёт оценку маркера. Отдельные «критические» пороги сразу переводят маркер в «внимание» или «стресс»:
например, правило Сам ≥ 0,5 — это сигнал рецессии, сколько бы ни показывали остальные метрики.

В быстрый индекс фон не входит: он меняется неделями и месяцами, а индекс — часами и днями.
"""
import time

STATES = {"ok": "норма", "warn": "внимание", "stress": "стресс", "nodata": "нет данных"}

MARKERS = {
    "labor": ("Рынок труда", "bg_labor"),
    "rates": ("Ставки 2Y / 10Y", "bg_rates"),
    "dollar": ("Доллар", "bg_dollar"),
    "credit": ("Кредит", "bg_credit"),
    "ai": ("ИИ-компании", "bg_ai"),
    "structure": ("Структура рынка", "bg_structure"),
}

# Какие точки проверки важны для маркера (виды событий из календаря)
WATCH = {"labor": ["jobs", "claims", "jolts"], "rates": ["fomc", "cpi", "pce"], "dollar": ["fomc"],
         "credit": ["jobs", "fomc"], "ai": ["earnings"], "structure": ["expiry", "fomc"]}

MEANING = {
    "labor": {
        "ok": "Рынок труда держится, рецессией не пахнет. ФРС не торопится смягчать — для BTC нейтрально.",
        "warn": "Рынок труда остывает: растут шансы снижения ставки (плюс для ликвидности), но и риск спада.",
        "stress": "Признаки рецессии. Обычно сначала бегство от риска и падение BTC вместе с акциями, "
                  "потом смягчение ФРС.",
    },
    "rates": {
        "ok": "Ставки не давят на рискованные активы.",
        "warn": "Движение ставок неблагоприятно для риска — следите за ФРС и инфляцией.",
        "stress": "Ставки резко давят на рискованные активы: дорогие деньги — главный враг BTC.",
    },
    "dollar": {
        "ok": "Долларов в системе хватает, фондирование спокойное.",
        "warn": "Доллар крепнет или в фондировании появилось напряжение — это давит на риск-активы.",
        "stress": "Признаки нехватки долларов. В такие периоды продают всё ликвидное, включая BTC.",
    },
    "credit": {
        "ok": "Рискованным заёмщикам дают деньги без лишней переплаты — аппетит к риску жив.",
        "warn": "Спреды расширяются или банки ужесточают выдачу — аппетит к риску слабеет.",
        "stress": "Кредит закрывается для слабых заёмщиков — классический предвестник распродаж.",
    },
    "ai": {
        "ok": "ИИ-стройка оплачивается из денег самого бизнеса — опора для Nasdaq, а через него и для BTC.",
        "warn": "Траты на ИИ растут быстрее денег от бизнеса — рынок станет чувствительнее к разочарованиям.",
        "stress": "Свободный денежный поток гиперскейлеров проседает — риск переоценки ИИ-историй и удара "
                  "по Nasdaq.",
    },
    "structure": {
        "ok": "Рост широкий: тянут не только лидеры, поэтому он устойчивее.",
        "warn": "Рынок держится на узкой группе лидеров — уязвим к развороту в них.",
        "stress": "Широкий рынок слабеет и лидеры сдают — фон риск-офф.",
    },
}


def clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def lin(x, full):
    return None if x is None else clip(x / full)


def fmt(v, f, suffix=""):
    return "—" if v is None else f.format(v) + suffix


class Marker:
    def __init__(self):
        self.rows, self.notes, self.floor = [], [], None  # floor: минимум состояния (warn/stress)

    def add(self, label, value_text, score, hint=""):
        self.rows.append({"label": label, "value": value_text, "score": score, "hint": hint})

    def at_least(self, state, note):
        order = ["ok", "warn", "stress"]
        if self.floor is None or order.index(state) > order.index(self.floor):
            self.floor = state
        self.notes.append(note)

    def result(self, headline, as_of, stale=False, extra=None):
        vals = [r["score"] for r in self.rows if r["score"] is not None]
        if not vals:
            return {"state": "nodata", "score": None, "headline": headline, "rows": self.rows,
                    "notes": self.notes, "as_of": as_of}
        score = sum(vals) / len(vals)
        state = "ok" if score > 0.2 else "stress" if score < -0.35 else "warn"
        order = ["ok", "warn", "stress"]
        if self.floor and order.index(self.floor) > order.index(state):
            state = self.floor
        out = {"state": state, "score": round(score, 3), "headline": headline, "rows": self.rows,
               "notes": self.notes, "as_of": as_of, "stale": stale}
        if extra:
            out.update(extra)
        return out


# ------------------------------------------------------------------ маркеры
def m_labor(d):
    m = Marker()
    sahm = d.get("sahm")
    if sahm is not None:
        m.add("Правило Сам", f"{sahm:.2f}", clip(1 - (sahm - 0.1) / 0.2),
              "рост безработицы от минимума; 0,5 и выше — сигнал рецессии")
        if sahm >= 0.5:
            m.at_least("stress", "Правило Сам сработало (≥ 0,5): исторически это совпадало с началом рецессий")
        elif sahm >= 0.3:
            m.at_least("warn", "Правило Сам приближается к порогу рецессии 0,5")
    if d.get("claims_4w") is not None:
        m.add("Заявки на пособие", f"{d['claims_4w'] / 1000:.0f} тыс. "
              f"({fmt(d.get('claims_chg_6m_pct'), '{:+.0f}', '%')} за 6 мес.)",
              None if d.get("claims_chg_6m_pct") is None else -lin(d["claims_chg_6m_pct"], 15),
              "среднее за 4 недели; быстрый рост — первые увольнения")
    if d.get("payrolls_3m_k") is not None:
        m.add("Новые рабочие места", f"{d['payrolls_3m_k']:+.0f} тыс./мес.",
              lin(d["payrolls_3m_k"] - 50, 100), "среднее за 3 месяца")
    if d.get("real_wage_yoy") is not None:
        m.add("Реальные зарплаты", f"{d['real_wage_yoy']:+.1f}% г/г",
              lin(d["real_wage_yoy"], 1.0), "рост зарплат минус инфляция")
    head = f"Безработица {fmt(d.get('unrate'), '{:.1f}', '%')}"
    if d.get("unrate_12m") is not None:
        head += f" ({d['unrate_12m']:+.1f} за год)"
    return m.result(head, d.get("as_of"), d.get("stale"))


def curve_regime(d2, d10):
    if d2 is None or d10 is None:
        return None, "нет данных", None
    steep, avg = d10 - d2, (d2 + d10) / 2
    if steep > 8:
        if avg >= 0:
            return "bear_steep", "медвежье укручение: растут длинные ставки, рынок требует премию за риск", -0.8
        return "bull_steep", "бычье укручение: рынок закладывает снижение ставок — часто перед спадом", -0.2
    if steep < -8:
        if avg >= 0:
            return "bear_flat", "медвежье уплощение: растут короткие ставки, ФРС звучит жёстче", -0.6
        return "bull_flat", "бычье уплощение: длинные ставки снижаются", 0.4
    return "flat", "кривая почти не меняется", -lin(d10, 25)


def m_rates(d):
    m = Marker()
    key, text, sc = curve_regime(d.get("d2_1m_bp"), d.get("d10_1m_bp"))
    m.add("За месяц", f"2Y {fmt(d.get('d2_1m_bp'), '{:+.0f}')}, 10Y {fmt(d.get('d10_1m_bp'), '{:+.0f}')} б.п.",
          sc, text)
    if key == "bear_steep" and (d.get("d10_1m_bp") or 0) > 30:
        m.at_least("stress", "Длинные ставки быстро растут — давление на всю «дальнюю» оценку, от Nasdaq до BTC")
    if d.get("real10") is not None:
        m.add("Реальная доходность 10Y", f"{d['real10']:.2f}%", clip((2.0 - d["real10"]) / 0.75),
              "доходность за вычетом инфляции; выше 2% — деньги дорогие")
    gap = d.get("y2_minus_ff_bp")
    if gap is not None:
        txt = (f"рынок закладывает снижение ставки примерно на {-gap:.0f} б.п." if gap < -10 else
               f"рынок закладывает повышение примерно на {gap:.0f} б.п." if gap > 10 else
               "рынок не ждёт изменения ставки")
        m.add("2Y против ставки ФРС", f"{gap:+.0f} б.п.", clip(-gap / 100, -0.5, 0.5), txt)
    curve = d.get("curve_bp")
    if curve is not None and (d.get("curve_min_1y_bp") or 0) < 0 < curve:
        m.at_least("warn", "Кривая вышла из инверсии за последний год — исторически это часто совпадало "
                           "с началом рецессий")
    head = (f"2Y {fmt(d.get('y2'), '{:.2f}', '%')} · 10Y {fmt(d.get('y10'), '{:.2f}', '%')} · "
            f"разница {fmt(curve, '{:+.0f}', ' б.п.')}")
    return m.result(head, d.get("as_of"), d.get("stale"), {"regime": key, "regime_text": text})


def m_dollar(d):
    m = Marker()
    if d.get("broad_1m_pct") is not None:
        m.add("Широкий индекс доллара", f"{d['broad_1m_pct']:+.1f}% за мес.", -lin(d["broad_1m_pct"], 2.0),
              "против корзины валют торговых партнёров США")
    x = d.get("sofr_iorb_max5_bp")
    if x is not None:
        m.add("Репо SOFR − ставка ФРС", f"{x:+.0f} б.п.",
              clip(0.5 - x / 7.5, -1, 0.5), "выше нуля — банкам не хватает денег на рынке репо")
        if x > 10:
            m.at_least("stress", "Ставка репо заметно выше ставки ФРС по резервам — напряжение в фондировании")
        elif x > 3:
            m.at_least("warn", "Ставка репо поднялась выше ставки по резервам")
    srf = d.get("srf_max5_bn")
    if srf is not None:
        m.add("Экстренное репо ФРС", f"{srf:.1f} млрд $", clip(0.3 - srf / 20, -1, 0.3),
              "банки занимают у ФРС, когда на рынке денег не хватает")
        if srf > 25:
            m.at_least("stress", "Крупные заимствования банков у ФРС через экстренное репо")
        elif srf > 5:
            m.at_least("warn", "Банки заметно пользуются экстренным репо ФРС")
    sw = d.get("swaps_bn")
    if sw is not None:
        m.add("Своп-линии ФРС", f"{sw:.1f} млрд $", clip(0.3 - sw / 5, -1, 0.3),
              "доллары, которые ФРС одалживает другим центробанкам")
        if sw > 10:
            m.at_least("stress", "Другим центробанкам срочно нужны доллары — глобальная нехватка")
    head = f"Индекс доллара {fmt(d.get('broad'), '{:.1f}')}"
    if d.get("broad_1m_pct") is not None:
        head += f" ({d['broad_1m_pct']:+.1f}% за месяц)"
    return m.result(head, d.get("as_of"), d.get("stale"))


def m_credit(d):
    m = Marker()
    hy = d.get("hy")
    if hy is not None:
        m.add("Спред мусорных бумаг", f"{hy:.2f}%", clip((4.5 - hy) / 1.5),
              "сколько сверх госбумаг платят рискованные компании")
        if hy > 6.5:
            m.at_least("stress", "Спред мусорных облигаций выше 6,5% — рынок закладывает волну дефолтов")
        if hy < 3.0:
            m.notes.append("Спреды у исторических минимумов: риск, возможно, недооценён")
    if d.get("hy_1m_bp") is not None:
        m.add("Изменение за месяц", f"{d['hy_1m_bp']:+.0f} б.п.", -lin(d["hy_1m_bp"], 60))
        if d["hy_1m_bp"] > 100:
            m.at_least("stress", "Спреды выросли больше чем на 1 п.п. за месяц — резкое бегство от риска")
    if d.get("ccc_1m_bp") is not None:
        m.add("Самые слабые (CCC)", f"{fmt(d.get('ccc'), '{:.1f}', '%')}, {d['ccc_1m_bp']:+.0f} б.п.",
              -lin(d["ccc_1m_bp"], 150), "спред и его изменение за месяц")
    if d.get("sloos") is not None:
        txt = "ужесточают" if d["sloos"] > 0 else "смягчают"
        m.add("Банки: кредиты бизнесу", f"{d['sloos']:+.0f}% {txt}", clip(-d["sloos"] / 20),
              f"опрос ФРС, квартал {d.get('sloos_as_of', '')}: доля банков, ужесточающих условия")
        if d["sloos"] > 30:
            m.at_least("warn", "Банки массово ужесточают выдачу кредитов бизнесу")
    head = f"HY {fmt(hy, '{:.2f}', '%')} · IG {fmt(d.get('ig'), '{:.2f}', '%')}"
    return m.result(head, d.get("as_of"), d.get("stale"))


def m_ai(d):
    m = Marker()
    if d.get("rev_yoy") is not None:
        m.add("Выручка гиперскейлеров", f"{d['rev_yoy']:+.0f}% г/г", lin(d["rev_yoy"] - 8, 8),
              "MSFT, GOOGL, AMZN, META, ORCL, за 12 месяцев")
    r = d.get("capex_to_ocf")
    if r is not None:
        m.add("Капекс / операц. поток", f"{r * 100:.0f}%", clip((0.75 - r) / 0.2),
              "какая доля заработанных денег уходит на стройку дата-центров")
    if d.get("fcf_bn") is not None:
        m.add("Свободный поток", f"{d['fcf_bn']:.0f} млрд $, {fmt(d.get('fcf_yoy'), '{:+.0f}', '%')} г/г",
              lin(d["fcf_yoy"], 25) if d.get("fcf_yoy") is not None else None,
              "операционный поток минус капекс")
        if d["fcf_bn"] < 0:
            m.at_least("stress", "Гиперскейлеры в сумме тратят на стройку больше, чем зарабатывают")
    if d.get("nvda_rev_yoy") is not None:
        m.add("Выручка Nvidia", f"{d['nvda_rev_yoy']:+.0f}% г/г", lin(d["nvda_rev_yoy"] - 20, 30),
              "главный поставщик: показывает спрос на ИИ-железо")
    cy, oy = d.get("capex_yoy"), d.get("ocf_yoy")
    if cy is not None and oy is not None and cy - oy > 20 and (d.get("fcf_yoy") or 0) < -15:
        m.at_least("warn", f"Капекс растёт на {cy:.0f}% при росте денежного потока на {oy:.0f}% — "
                           "стройка всё больше за счёт запасов и долга")
    head = (f"Капекс {fmt(d.get('capex_bn'), '{:.0f}', ' млрд $')} за год "
            f"({fmt(cy, '{:+.0f}', '%')})")
    return m.result(head, d.get("latest_filed"), d.get("stale"))


def m_structure(d, dom_30d_ago=None):
    m = Marker()
    dd = d.get("ndx_dd_pct")
    if dd is not None:
        m.add("Nasdaq от максимума", f"−{dd:.1f}%", clip(1 - dd / 7.5), "просадка от пика за год")
        if dd > 20:
            m.at_least("stress", "Nasdaq в медвежьем рынке: больше 20% от максимума")
    if d.get("rsp_spy_1m") is not None:
        m.add("Ширина S&P 500", f"{d['rsp_spy_1m']:+.1f}% за мес.", lin(d["rsp_spy_1m"], 2.0),
              "равновзвешенный индекс против обычного: минус — тянут только гиганты")
    if d.get("qqew_qqq_1m") is not None:
        m.add("Ширина Nasdaq-100", f"{d['qqew_qqq_1m']:+.1f}% за мес.", lin(d["qqew_qqq_1m"], 2.0),
              "равновзвешенный Nasdaq-100 против обычного")
    if d.get("iwm_qqq_1m") is not None:
        m.add("Малые против Nasdaq", f"{d['iwm_qqq_1m']:+.1f}% за мес.", lin(d["iwm_qqq_1m"], 3.0),
              "Russell 2000 против Nasdaq-100")
    dom = d.get("btc_dom")
    if dom is not None:
        chg = None if dom_30d_ago is None else dom - dom_30d_ago
        m.add("Доля BTC в крипте", f"{dom:.1f}%" + ("" if chg is None else f", {chg:+.1f} п.п. за мес."),
              None if chg is None else -lin(chg, 3.0) * 0.5,
              "рост доли — деньги уходят из альткоинов в BTC, аппетит к риску сужается")
    c = d.get("btc_ndx_corr30")
    corr_note = None
    if c is not None:
        corr_note = (f"BTC сейчас ходит вместе с Nasdaq (корреляция {c:.2f}): макро важнее крипто-новостей"
                     if c > 0.5 else f"BTC живёт своей жизнью (корреляция с Nasdaq {c:.2f})" if c < 0.2 else
                     f"Связь BTC с Nasdaq умеренная (корреляция {c:.2f})")
        m.notes.append(corr_note)
    head = f"Nasdaq {fmt(d.get('ndx_1m_pct'), '{:+.1f}', '%')} за месяц"
    return m.result(head, d.get("as_of"), d.get("stale"), {"corr": c})


FUNCS = {"labor": m_labor, "rates": m_rates, "dollar": m_dollar, "credit": m_credit, "ai": m_ai,
         "structure": m_structure}


def upcoming(raw, kinds=None, limit=8, since_hours=0):
    """Ближайшие точки проверки (и ориентировочные отчёты ИИ-компаний)."""
    cal = raw.get("bg_calendar", {})
    events = list(cal.get("events", [])) if isinstance(cal, dict) else []
    ai = raw.get("bg_ai", {})
    for tic, c in (ai.get("companies", {}) if isinstance(ai, dict) else {}).items():
        if c.get("filed"):
            t = time.mktime(time.strptime(c["filed"], "%Y-%m-%d")) + 91 * 86400
            while t < time.time() - 7 * 86400:
                t += 91 * 86400
            events.append({"kind": "earnings", "title": f"Отчёт {tic}", "ts": t, "imp": 2, "approx": True})
    now = time.time() - since_hours * 3600
    ev = sorted((e for e in events if e["ts"] >= now and (kinds is None or e["kind"] in kinds)),
                key=lambda e: e["ts"])
    return ev[:limit]


def score_background(raw, dom_30d_ago=None):
    out = {}
    for key, (name, src) in MARKERS.items():
        d = raw.get(src)
        if not isinstance(d, dict) or not d or "error" in d:
            res = {"state": "nodata", "score": None, "headline": "нет данных", "rows": [], "notes": [],
                   "error": d.get("error") if isinstance(d, dict) else None}
        else:
            try:
                res = FUNCS[key](d, dom_30d_ago) if key == "structure" else FUNCS[key](d)
            except Exception as e:  # noqa: BLE001
                res = {"state": "nodata", "score": None, "headline": "ошибка расчёта", "rows": [], "notes": [],
                       "error": str(e)[:150]}
        res["meaning"] = MEANING[key].get(res["state"], "")
        nxt = upcoming(raw, WATCH[key], limit=20)
        top = [e for e in nxt if e.get("imp", 0) >= 3 and e["ts"] - time.time() < 31 * 86400]
        res["next"] = (top or nxt or [None])[0]
        out[key] = res
    scores = [m["score"] for m in out.values() if m.get("score") is not None]
    avg = sum(scores) / len(scores) if scores else None
    n_stress = sum(m["state"] == "stress" for m in out.values())
    n_warn = sum(m["state"] == "warn" for m in out.values())
    if avg is None:
        label, state = "нет данных", "nodata"
    elif n_stress >= 2 or avg < -0.3:
        label, state = "Фон напряжённый", "stress"
    elif n_stress == 1 or n_warn >= 3 or avg < 0.15:
        label, state = "Фон смешанный", "warn"
    else:
        label, state = "Фон благоприятный", "ok"
    return {"markers": out, "score": avg, "label": label, "state": state, "n_stress": n_stress, "n_warn": n_warn}
