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
