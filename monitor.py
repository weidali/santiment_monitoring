"""Запуск: python monitor.py         — непрерывно, каждые INTERVAL_SECONDS
           python monitor.py --once  — один проход (удобно для cron)"""
import sys
import time

import alerts
import scoring
import sources
import storage
from config import BASE_DIR, INTERVAL_SECONDS

PRUNE_MARK = BASE_DIR / ".cache" / "last_prune"


def maybe_prune():
    """Раз в 6 часов ужимает старые замеры (см. storage.prune)."""
    try:
        if PRUNE_MARK.exists() and time.time() - PRUNE_MARK.stat().st_mtime < 6 * 3600:
            return
        PRUNE_MARK.parent.mkdir(exist_ok=True)
        slimmed, deleted = storage.prune()
        PRUNE_MARK.touch()
        if slimmed or deleted:
            print(f"База: ужато замеров {slimmed}, удалено {deleted}")
    except Exception as e:  # noqa: BLE001
        print("Не удалось ужать базу:", e)


def run_once():
    raw = sources.collect_all()
    scored = scoring.score(raw)
    storage.save(raw, scored)
    alerts.check(raw, scored)
    maybe_prune()

    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M')}  Индекс {scored['composite']:+.0f}  |  {scored['regime']}")
    print(f"Покрытие данных: {scored['coverage']:.0%}")
    for g, s in scored["groups"].items():
        print(f"  {g:12s} {s:+.2f}")
    for s in scored["signals"].values():
        print(f"    [{s['value']:+.1f}] {s['comment']}")
    for name, passed in scored["confirmed"]["checks"].items():
        print(f"  {'✔' if passed else '·'} {name}")
    bg = scored.get("background")
    if bg:
        from background import MARKERS, STATES
        print(f"Фон: {bg['label']}")
        for k, m in bg["markers"].items():
            print(f"  {MARKERS[k][0]:18s} {STATES[m['state']]:10s} {m.get('headline', '')}")
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
