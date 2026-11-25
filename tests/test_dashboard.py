"""python tests/test_dashboard.py   (no Metabase needed: queries are answered from the snapshot)"""
import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import pipeline as p  # noqa: E402
p.optional = lambda *a: None          # sql/ files are only run live
from logic import Model, spike_pct  # noqa: E402
from render import render  # noqa: E402

SNAP = json.loads((ROOT / "data" / "snapshot.json").read_text(encoding="utf-8"))
NOW = dt.datetime(2026, 11, 25, 9, 15, tzinfo=p.IST)


def fake_query(sql, params=None):
    """Answers each pipeline query with the rows Snowflake would return for the snapshot."""
    import re
    lo, hi = re.findall(r"CALL_TIME::DATE [<>]= DATE '([\d-]+)'", sql)[-2:]
    days = [d for d in SNAP["facts"] if lo <= d <= hi]
    if "AS REP" in sql:
        return [{"D": d, "QG": q, "DEN": SNAP[q][d]["repeat_den"], "REP": SNAP[q][d]["repeat"]}
                for q in ("svc", "wn") for d in days]
    if "AS MISSED" in sql:
        return [{"D": d, "QG": q, **{k.upper(): SNAP[q][d].get(k) for k in
                 ("vol", "missed", "transferred", "low", "agents", "aht")}} for q in ("svc", "wn") for d in days]
    if "COUNT(DISTINCT USER_NAME)" in sql:
        return [{"D": d, "H": h, "N": n} for d, hs in SNAP["wn_mw"].items() for h, n in hs.items()]
    if "HOUR(CALL_TIME)" in sql:
        rows = []
        for d, hs in SNAP["hourly"].items():
            for h, cs in hs.items():
                for c, codes in cs.items():
                    for k, n in codes.items():
                        missed = c == "Missed"
                        rows.append({"D": d, "H": h, "QG": "svc", "CLS": None if missed else c,
                                     "CODE": None if missed else k, "N": n})
        for d, hs in SNAP["wn_hourly"].items():
            for h, cs in hs.items():
                rows += [{"D": d, "H": h, "QG": "wn", "CLS": None if c == "Missed" else c, "CODE": "x", "N": n}
                         for c, n in cs.items()]
        return rows
    rows = [{"D": d, "QG": "svc", "CLS": None, "CODE": None, "N": 7} for d in days]     # must be ignored
    for d in days:
        for c, codes in SNAP["facts"][d].items():
            for k, n in codes.items():
                missed = c == "Missed"
                rows.append({"D": d, "QG": "wn" if missed or c == "Wiom Net" else "svc", "CLS": None if missed else c, "CODE": k, "N": n})
    return rows


class Tests(unittest.TestCase):
    maxDiff = None

    def test_refresh_reproduces_snapshot_page(self):
        for full in (False, True):
            s = p.refresh(SNAP, now=NOW, full=full, query=fake_query)
            self.assertEqual(s["facts"], SNAP["facts"])
            self.assertEqual(s["wn_hourly"], SNAP["wn_hourly"])
            # June "low" counts are fractional in the snapshot (recovered from rounded percentages)
            s["svc"] = {d: {**v, "low": SNAP["svc"][d]["low"]} for d, v in s["svc"].items()}
            s["as_of"] = SNAP["as_of"]
            a, b = render(SNAP).split("\n"), render(s).split("\n")
            diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y and "PAYLOAD" not in x]
            self.assertEqual(diff, [])

    def test_new_day_rolls_over(self):
        s = p.refresh(SNAP, now=NOW + dt.timedelta(days=1), query=lambda q, params=None: [])
        m = Model(s)
        self.assertEqual(m.today, "2026-11-26")
        self.assertEqual(m.total("2026-11-26"), 0)
        self.assertIn("Today (26 Nov 2026)", render(s))

    def test_new_month_gets_a_tab(self):
        s = p.refresh(SNAP, now=NOW + dt.timedelta(days=7), query=lambda q, params=None: [])
        self.assertIn('data-target="tab-2026-12">Dec 2026', render(s))

    def test_spike_thresholds(self):
        for v, ref, hit in ((115, 100, True), (114, 100, False), (25, 20, True), (24, 20, False),
                            (10, 5, True), (9, 5, False), (5, 0, False)):
            self.assertEqual(spike_pct(v, ref) is not None, hit, (v, ref))

    def test_raw_sql_matches_original(self):
        sql, params = p.raw_sql("Change Request", "Wifi Name / Password", "day", "2026-11-24", "2026-11")
        self.assertEqual(
            sql.replace("{{cls}}", "'Change Request'").replace("{{code}}", "'Wifi Name / Password'"),
            "SELECT CALL_ID, CALL_TIME, QUEUE_NAME, PHONE, USER_NAME, DISPOSITION_CLASS, DISPOSITION_CODE, "
            "CUSTOMER_TALK_TIME, HANGUP_DETAILS, CALL_NOTES FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT WHERE "
            "QUEUE_NAME IN ('high_pain_queue','low_pain_queue') AND CALL_TYPE = 'inbound.call.dial' AND "
            "CALL_TIME::DATE = DATE '2026-11-24' AND DISPOSITION_CLASS = 'Change Request' AND "
            "DISPOSITION_CODE = 'Wifi Name / Password' ORDER BY CALL_TIME DESC")
        sql, _ = p.raw_sql("Sales Queue", "", "prev", "", "2026-12")
        self.assertIn("CALL_TIME::DATE < DATE '2027-01-01'", sql)

    def test_raw_rejects_bad_input(self):
        for args in (("x", "", "day", "2026-11-24' OR 1=1", ""), ("x", "", "mtd", "", "2026-13"), ("x", "", "all", "", "")):
            with self.assertRaises(ValueError):
                p.raw_sql(*args)
        sql, params = p.raw_sql("a' OR '1'='1", "", "day", "2026-11-24", "")
        self.assertNotIn("OR '1'", sql)
        self.assertEqual(params["cls"], "a' OR '1'='1")


if __name__ == "__main__":
    unittest.main()
