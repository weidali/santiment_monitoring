"""Запуск: python monitor.py         — непрерывно, каждые INTERVAL_SECONDS
           python monitor.py --once  — один проход (удобно для cron)"""
import sys
import time

import alerts
import scoring
import sources
import storage
from config import INTERVAL_SECONDS


def run_once():
    raw = sources.collect_all()
    scored = scoring.score(raw)
    storage.save(raw, scored)
    alerts.check(raw, scored)

    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M')}  Индекс {scored['composite']:+.0f}  |  {scored['regime']}")
    print(f"Покрытие данных: {scored['coverage']:.0%}")
    for g, s in scored["groups"].items():
        print(f"  {g:12s} {s:+.2f}")
    for s in scored["signals"].values():
        print(f"    [{s['value']:+.1f}] {s['comment']}")
    for name, passed in scored["confirmed"]["checks"].items():
        print(f"  {'✔' if passed else '·'} {name}")
    errors = {k: v["error"] for k, v in raw.items() if isinstance(v, dict) and "error" in v}
    for k, e in errors.items():
        print(f"  ! {k}: {e}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        while True:
            run_once()
            time.sleep(INTERVAL_SECONDS)
