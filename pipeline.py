"""Pulls fresh numbers from Metabase into the store.

Checked against the original's published numbers on 25 Nov 2026 (verify_live.py repeats the check).
CSAT, plans, device counts and the CSP/ISP split have their own files in sql/.
"""
import datetime as dt
import re
from collections import Counter
from pathlib import Path

import callback
import metabase
from logic import FLAT_CLASSES, HOURLY_DAYS, HOURS, SALES_QUEUE_CLASSES, WN_HOURS, add_days

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TABLE = metabase.env("CALLS_TABLE", "PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT")
SVC_QUEUES = ("high_pain_queue", "low_pain_queue")
WN_QUEUE = metabase.env("WIOM_NET_QUEUE", "Wiom_Net_Queue")
WN_START = "2026-09-01"              # first day the Wiom Net queue is counted
CALL_TYPE = "inbound.call.dial"
CSAT_START = "2026-04-01"            # earlier months keep the published scores
RELOAD_DAYS = 3                      # complete days re-read on every refresh
SQL_DIR = Path(__file__).with_name("sql")

for _name in (TABLE, WN_QUEUE):
    if not re.fullmatch(r"[\w.$ -]+", _name):
        raise ValueError(f"Invalid name in settings: {_name!r}")

MISSED = "(DISPOSITION_CLASS IS NULL OR TRIM(DISPOSITION_CLASS) = '')"
SVC = "QUEUE_NAME IN ('" + "','".join(SVC_QUEUES) + "')"
WN = f"(QUEUE_NAME = '{WN_QUEUE}' AND CALL_TIME::DATE >= DATE '{WN_START}')"
BOTH = f"({SVC} OR {WN})"
Q = f"IFF({SVC}, 'svc', 'wn')"
INBOUND = f"CALL_TYPE = '{CALL_TYPE}'"
WN_OPEN = f"({SVC} OR HOUR(CALL_TIME) BETWEEN {WN_HOURS[0]} AND {WN_HOURS[-1]})"


def seconds(col):
    """HH:MM:SS text column -> seconds"""
    part = lambda i: f"COALESCE(TRY_TO_NUMBER(SPLIT_PART({col}, ':', {i})), 0)"
    return f"({part(1)} * 3600 + {part(2)} * 60 + {part(3)})"


TALK = seconds("CUSTOMER_TALK_TIME")
HANDLE = f"({TALK} + {seconds('ACW_DURATION')})"       # talk + wrap-up


def span(lo, hi):
    return f"CALL_TIME::DATE >= DATE '{lo}' AND CALL_TIME::DATE <= DATE '{hi}'"


# ── SQL ─────────────────────────────────────────────────────────────────────
def sql_daily(lo, hi):
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, {Q} AS QG,
       TRIM(DISPOSITION_CLASS) AS CLS, TRIM(DISPOSITION_CODE) AS CODE, COUNT(*) AS N
FROM {TABLE} WHERE {BOTH} AND {INBOUND} AND {span(lo, hi)} GROUP BY 1, 2, 3, 4"""


def sql_hourly(lo, hi):
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, HOUR(CALL_TIME) AS H, {Q} AS QG,
       TRIM(DISPOSITION_CLASS) AS CLS, TRIM(DISPOSITION_CODE) AS CODE, COUNT(*) AS N
FROM {TABLE} WHERE {BOTH} AND {INBOUND} AND {span(lo, hi)} GROUP BY 1, 2, 3, 4, 5"""


def sql_manpower(lo, hi):
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, HOUR(CALL_TIME) AS H,
       COUNT(DISTINCT USER_NAME) AS N
FROM {TABLE} WHERE {WN} AND {INBOUND} AND {span(lo, hi)} AND NULLIF(TRIM(USER_NAME), '') IS NOT NULL GROUP BY 1, 2"""


def sql_metrics(lo, hi):
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, {Q} AS QG, COUNT(*) AS VOL,
       COUNT_IF({MISSED}) AS MISSED,
       COUNT_IF(DISPOSITION_CODE = 'user.transferred.to.aq') AS TRANSFERRED,
       COUNT_IF(QUEUE_NAME = 'low_pain_queue') AS LOW,
       COUNT(DISTINCT NULLIF(TRIM(USER_NAME), '')) AS AGENTS,
       AVG(IFF({TALK} > 0, {HANDLE}, NULL)) AS AHT
FROM {TABLE} WHERE {BOTH} AND {WN_OPEN} AND {INBOUND} AND {span(lo, hi)} GROUP BY 1, 2"""


def sql_repeat(lo, hi):
    """Repeat = calls beyond a number's first call that day (Wiom Net: all hours)."""
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, {Q} AS QG, COUNT(*) AS DEN,
       COUNT(*) - COUNT(DISTINCT PHONE) AS REP
FROM {TABLE} WHERE {BOTH} AND {INBOUND} AND {span(lo, hi)} GROUP BY 1, 2"""


# ── rows -> store ───────────────────────────────────────────────────────────
def roll_up(cls):
    return "Sales Queue" if cls in SALES_QUEUE_CLASSES else cls


def facts_from(rows):
    """Daily categories: Service calls with a disposition, plus all Wiom Net calls."""
    out = {}
    for r in rows:
        cls = roll_up(r["CLS"] or "")
        if r["QG"] == "svc" and cls in ("", "Wiom Net"):
            continue                     # missed Service calls live in the metrics table only;
        if not cls:                      # the Wiom Net class comes from its own queue only
            cls = "Missed"
        code = "" if cls in FLAT_CLASSES else (r["CODE"] or "")
        day = out.setdefault(r["D"], {}).setdefault(cls, Counter())
        day[code] += int(r["N"])
    return {d: {c: dict(v) for c, v in x.items()} for d, x in out.items()}


def hourly_from(rows):
    svc, wn = {}, {}
    for r in rows:
        cls, n, h = roll_up(r["CLS"] or "") or "Missed", int(r["N"]), str(int(r["H"]))
        if r["QG"] == "wn":
            wn.setdefault(r["D"], {}).setdefault(h, Counter())[cls] += n
        else:
            svc.setdefault(r["D"], {}).setdefault(h, {}).setdefault(cls, Counter())[r["CODE"] or cls] += n
    return svc, wn


def isd_from(rows):
    """Hourly 'Internet Supply Down' calls -> top 5 partners and top 3 ISPs per hour."""
    acc = {}
    for r in rows:
        csp, isp = acc.setdefault(r["D"], {}).setdefault(str(int(r["H"])), (Counter(), Counter()))
        csp[r["CSP"] or "Unknown"] += int(r["N"])
        isp[r["ISP"] or "Unknown"] += int(r["N"])
    return {d: {h: {"csp": [list(x) for x in c.most_common(5)], "isp": [list(x) for x in i.most_common(3)]}
                for h, (c, i) in hs.items()} for d, hs in acc.items()}


def optional(name, lo, hi):
    """Rows of sql/<name>.sql ({LO}/{HI} filled with dates), or None when the file is absent."""
    f = SQL_DIR / f"{name}.sql"
    if not f.exists():
        return None
    return metabase.query(f.read_text(encoding="utf-8").replace("{LO}", lo).replace("{HI}", hi))


def refresh(store, now=None, full=False, query=None):
    """Return a new store with everything from the last few days (or all history) re-read."""
    query = query or metabase.query
    now = now or dt.datetime.now(IST)
    today = now.date().isoformat()
    first = min(store["facts"])
    recent = min(max(store["facts"]), add_days(today, -RELOAD_DAYS))
    lo = first if full else recent
    hlo = add_days(today, -(HOURLY_DAYS - 1))

    s = dict(store)
    for k in ("facts", "svc", "wn", "isd", "months"):
        s[k] = dict(store[k])
    in_range = lambda d: lo <= d <= today

    # categories
    daily = facts_from(query(sql_daily(lo, today)))
    s["facts"] = {d: v for d, v in s["facts"].items() if not in_range(d)} | daily
    s["facts"].setdefault(today, {})

    # queue metrics (CSAT comes from its own optional query)
    for q in ("svc", "wn"):
        kept = {d: v for d, v in s[q].items() if not in_range(d)}
        csat = {d: v.get("csat") for d, v in s[q].items()}
        s[q] = kept
        for d in daily | {today: {}}:
            s[q][d] = {"vol": 0, "missed": 0, "transferred": 0, "low": 0, "agents": 0, "aht": 0,
                       "repeat": 0, "repeat_den": 0, "csat": csat.get(d)}
    for r in query(sql_metrics(lo, today)):
        rec = s[r["QG"]].get(r["D"])
        if rec is not None:
            rec.update({k.lower(): int(r[k] or 0) for k in ("VOL", "MISSED", "TRANSFERRED", "LOW", "AGENTS")})
            rec["aht"] = float(r["AHT"] or 0)
    for r in query(sql_repeat(lo, today)):
        rec = s[r["QG"]].get(r["D"])
        if rec is not None:
            rec["repeat"], rec["repeat_den"] = int(r["REP"] or 0), int(r["DEN"] or 0)

    # Current tab
    s["hourly"], s["wn_hourly"] = hourly_from(query(sql_hourly(hlo, today)))
    s["wn_mw"] = {}
    for r in query(sql_manpower(hlo, today)):
        s["wn_mw"].setdefault(r["D"], {})[str(int(r["H"]))] = int(r["N"])

    # Callback Calls tab
    old = s.get("callback") or {}
    clo = max(callback.START, first) if full or not old else lo
    fresh = callback.from_rows(query(callback.sql_daily(TABLE, clo, today, TALK, HANDLE)),
                               query(callback.sql_hours(TABLE, clo, today)),
                               query(callback.sql_cats(TABLE, clo, today)), roll_up)
    s["callback"] = {d: v for d, v in old.items() if d < clo} | fresh

    # optional sources: kept as they are until a query file is supplied
    rows = optional("isd_hourly", max(lo, hlo), today)
    if rows is not None:
        s["isd"].update(isd_from(r for r in rows if int(r["H"]) in HOURS))
    rows = optional("csat_daily", max(lo[:7] + "-01", CSAT_START), today)              # D, QG, POSITIVE, TOTAL
    if rows is not None:
        pos, tot = Counter(), Counter()
        for r in rows:
            p, t = int(r["POSITIVE"] or 0), int(r["TOTAL"] or 0)
            if r["D"] in s[r["QG"]] and t:
                s[r["QG"]][r["D"]] = {**s[r["QG"]][r["D"]], "csat": 100 * p / t}
            pos[r["QG"], r["D"][:7]] += p
            tot[r["QG"], r["D"][:7]] += t
        for (q, ym), t in tot.items():
            if t:
                s["months"][ym] = {**s["months"].get(ym, {}), f"csat_{q}": round(100 * pos[q, ym] / t, 1)}
    rows = optional("devices_monthly", recent, today)         # YM, PAYING, ACTIVE
    for r in rows or []:
        s["months"][r["YM"]] = {**s["months"].get(r["YM"], {}), "paying": int(r["PAYING"]), "active": int(r["ACTIVE"])}
    rows = optional("plans_daily", lo, today)             # D, PLANS_SOLD
    if rows is not None:
        calls = {p["date"]: p["wiom_net_calls"] for p in s["plans"]}
        calls.update({d: v["repeat_den"] for d, v in s["wn"].items() if v.get("repeat_den")})
        start = s["plans"][0]["date"] if s["plans"] else first
        rows = [r for r in rows if r["D"] >= start]
        sold = {p["date"]: p["plans_sold"] for p in s["plans"]} | {r["D"]: int(r["PLANS_SOLD"]) for r in rows}
        s["plans"] = [{"date": d, "plans_sold": n, "wiom_net_calls": calls.get(d, 0)}
                      for d, n in sorted(sold.items()) if d < today]

    s["today"], s["now_hour"] = today, now.hour
    s["as_of"] = now.strftime("%Y-%m-%d %H:%M") + " IST"
    return s


# ── drill-through ───────────────────────────────────────────────────────────
RAW_COLS = ["CALL_ID", "CALL_TIME", "QUEUE_NAME", "PHONE", "USER_NAME", "DISPOSITION_CLASS",
            "DISPOSITION_CODE", "CUSTOMER_TALK_TIME", "HANGUP_DETAILS", "CALL_NOTES"]


def raw_sql(cls, code, scope, date, ym):
    """(sql, params) for the records behind one cell.

    Like the original, records come from the two Service queues only, so cells that include
    Wiom Net calls list fewer records. DRILL_MATCH_CELL=1 lists exactly what the cell counts."""
    match_cell = metabase.env("DRILL_MATCH_CELL") == "1"
    if scope == "day":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError("bad date")
        dt.date.fromisoformat(date)
        when = f"CALL_TIME::DATE = DATE '{date}'"
    elif scope in ("mtd", "prev"):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", ym):
            raise ValueError("bad month")
        y, m = map(int, ym.split("-"))
        end = dt.date(y + m // 12, m % 12 + 1, 1)
        when = f"CALL_TIME::DATE >= DATE '{ym}-01' AND CALL_TIME::DATE < DATE '{end}'"
    else:
        raise ValueError("bad scope")
    params, queues = {}, BOTH if match_cell else SVC
    if cls == "Missed":
        what = MISSED
        queues = WN if match_cell else SVC
    elif cls == "Sales Queue":
        what = "DISPOSITION_CLASS IN ('" + "','".join(SALES_QUEUE_CLASSES) + "')"
    else:
        what, params["cls"] = "DISPOSITION_CLASS = {{cls}}", cls
    if code:
        what += " AND DISPOSITION_CODE = {{code}}"
        params["code"] = code
    return (f"SELECT {', '.join(RAW_COLS)} FROM {TABLE} WHERE {queues} AND {INBOUND} AND {when} "
            f"AND {what} ORDER BY CALL_TIME DESC"), params


def raw_records(cls, code, scope, date, ym, query=None):
    sql, params = raw_sql(cls, code, scope, date, ym)
    rows = (query or metabase.query)(sql, params)
    return {"cols": RAW_COLS, "rows": [[r.get(c) for c in RAW_COLS] for r in rows], "count": len(rows)}
