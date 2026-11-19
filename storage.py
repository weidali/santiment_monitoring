import json
import sqlite3
import time

from config import DB_PATH


def conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        ts INTEGER PRIMARY KEY, composite REAL, regime TEXT, raw TEXT, scored TEXT)""")
    return c


def save(raw, scored):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
                  (int(time.time()), scored["composite"], scored["regime"],
                   json.dumps(raw, default=str), json.dumps(scored, default=str)))


def last(n=1, before_ts=None):
    q = "SELECT ts, composite, regime, raw, scored FROM snapshots"
    args = []
    if before_ts:
        q += " WHERE ts <= ?"
        args.append(before_ts)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(n)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    return [{"ts": r[0], "composite": r[1], "regime": r[2],
             "raw": json.loads(r[3]), "scored": json.loads(r[4])} for r in rows]


def history(days=30):
    since = int(time.time()) - days * 86400
    with conn() as c:
        rows = c.execute("SELECT ts, composite, raw, scored FROM snapshots WHERE ts >= ? ORDER BY ts",
                         (since,)).fetchall()
    return [{"ts": r[0], "composite": r[1], "raw": json.loads(r[2]), "scored": json.loads(r[3])} for r in rows]


def prune(keep_full_days=1, thin_after_days=14):
    """Не даёт базе разрастаться:
    — в замерах старше суток убирает то, что нужно только для текущего экрана
      (календарь, подробные строки фоновых маркеров), состояния и цифры остаются;
    — замеры старше двух недель прореживает до одного в час.
    Вызывается из monitor.py несколько раз в сутки."""
    now = int(time.time())
    slim_before, thin_before = now - keep_full_days * 86400, now - thin_after_days * 86400
    with conn() as c:
        rows = c.execute("SELECT ts, raw, scored FROM snapshots WHERE ts < ? AND "
                         "(raw LIKE '%\"bg_calendar\"%' OR scored LIKE '%\"rows\"%')", (slim_before,)).fetchall()
        for ts, raw_s, sc_s in rows:
            raw, sc = json.loads(raw_s), json.loads(sc_s)
            raw.pop("bg_calendar", None)
            for m in ((sc.get("background") or {}).get("markers") or {}).values():
                for k in ("rows", "notes", "meaning", "next"):
                    m.pop(k, None)
            c.execute("UPDATE snapshots SET raw = ?, scored = ? WHERE ts = ?",
                      (json.dumps(raw, default=str), json.dumps(sc, default=str), ts))
        cur = c.execute("DELETE FROM snapshots WHERE ts < ? AND ts NOT IN "
                        "(SELECT MIN(ts) FROM snapshots WHERE ts < ? GROUP BY ts / 3600)",
                        (thin_before, thin_before))
        deleted = cur.rowcount
    if deleted > 500:
        c = sqlite3.connect(DB_PATH)
        c.execute("VACUUM")
        c.close()
    return len(rows), deleted
