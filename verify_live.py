"""Compares the live SQL with the numbers the original dashboard published (read-only).

    python verify_live.py                      last three complete days of the snapshot
    python verify_live.py 2026-11-20 2026-11-24

Prints aggregates only, no customer data."""
import json
import sys
from pathlib import Path

import metabase
import pipeline as p
from logic import Model, mmss

snap = json.loads((Path(__file__).with_name("data") / "snapshot.json").read_text(encoding="utf-8"))
m = Model(snap)
done = [d for d in m.days if d != m.today]
lo, hi = (sys.argv[1:3] if len(sys.argv) > 2 else (done[-3], done[-1]))
days = [d for d in done if lo <= d <= hi]

print("Queue names with inbound calls in the period:")
for r in metabase.query(f"SELECT QUEUE_NAME AS Q, CALL_TYPE AS T, COUNT(*) AS N FROM {p.TABLE} "
                        f"WHERE {p.span(lo, hi)} AND CALL_TYPE ILIKE '%inbound%' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20"):
    print(f"   {r['Q']!s:30} {r['T']!s:24} {r['N']}")

live = p.facts_from(metabase.query(p.sql_daily(lo, hi)))
cells = bad = 0
for d in days:
    for cls in set(snap["facts"][d]) | set(live.get(d, {})):
        a, b = snap["facts"][d].get(cls, {}), live.get(d, {}).get(cls, {})
        for code in set(a) | set(b):
            cells += 1
            if a.get(code, 0) != b.get(code, 0):
                bad += 1
                if bad <= 15:
                    print(f"   differs {d} {cls} > {code or '-'}: dashboard {a.get(code, 0)}, live {b.get(code, 0)}")
print(f"\nCategory cells: {cells - bad} of {cells} equal")

rows = {(r["QG"], r["D"]): r for r in metabase.query(p.sql_metrics(lo, hi))}
rep = {(r["QG"], r["D"]): r for r in metabase.query(p.sql_repeat(lo, hi))}
print("\nMetrics, dashboard / live")
for q in ("svc", "wn"):
    for d in days:
        s, r, x = snap[q][d], rows.get((q, d)), rep.get((q, d), {})
        if not r:
            print(f"   {q} {d}: no live rows")
            continue
        pct = lambda a, b: f"{100 * int(a or 0) / int(b):.1f}%" if b and int(b) else "-"
        print(f"   {q:3} {d}  volume {s['vol']}/{r['VOL']}  missed {s['missed']}/{r['MISSED']}  "
              f"AHT {mmss(s['aht'])}/{mmss(float(r['AHT'] or 0))}  agents {s['agents']}/{r['AGENTS']}  "
              f"transferred {s['transferred']}/{r['TRANSFERRED']}  "
              f"repeat {pct(s['repeat'], s['repeat_den'])}/{pct(x.get('REP'), x.get('DEN'))}"
              + (f"  low pain {pct(s['low'], s['vol'])}/{pct(r['LOW'], r['VOL'])}" if q == "svc" else ""))
