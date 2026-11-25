"""Data logic of the inbound-call dashboard: pure functions, no I/O.

The "store" is a plain dict (data/snapshot.json, data/store.json):
  as_of        text shown as Last Refreshed
  today        "YYYY-MM-DD" (IST)
  now_hour     current IST hour; hours of today before it count as complete
  facts        {date: {class: {code: calls}}}      Service non-missed + all Wiom Net calls
  hourly       {date: {hour: {class: {code: n}}}}  Service queue, all calls incl. missed
  wn_hourly    {date: {hour: {class: n}}}          Wiom Net queue
  wn_mw        {date: {hour: agents}}              distinct Wiom Net agents per hour
  isd          {date: {hour: {csp: [[name, n] x5], isp: [[name, n] x3]}}}
  svc / wn     {date: {vol, missed, transferred, repeat, repeat_den, low, aht, agents, csat}}
  plans        [{date, plans_sold, wiom_net_calls}]
  months       {ym: {paying, active, csat_svc, csat_wn}}
  base         averages of the month before the first full month (no daily data for it)
"""
import calendar
import datetime as dt
from collections import Counter

# ── taxonomy ────────────────────────────────────────────────────────────────
CLASS_ORDER = [
    "Internet Related Issues", "Payment Related Issues", "Others", "Change Request",
    "Partner Misbehavior", "Refund", "Remove Connection - Talk to Customer",
    "Shifting Request", "Wiom Net", "Incomplete Call", "campaign.system.disposition",
    "user.forced.logged.off", "user.transferred.to.campaign",
]
CLASS_TAIL = ["Sales Queue", "Booking Queue", "Missed"]
# Old sales dispositions are rolled up into one "Sales Queue" row.
SALES_QUEUE_CLASSES = [
    "Sales-App Issues", "Sales-Next Steps", "Sales-Next Steps - Old Contruct",
    "Sales-Pause", "Sales-System Understanding", "Sales-System Understanding - Old",
]
# Shown as a single row, without sub-category rows.
FLAT_CLASSES = {"user.forced.logged.off", "user.transferred.to.campaign", *CLASS_TAIL}

HOURS = list(range(8, 23))
WN_HOURS = list(range(9, 22))
HOURLY_DAYS = 9          # dates offered on the Current tab (today + 8)
TOP_CODES = 15
MAX_CHIPS = 8
# Month whose comparison base is a custom window of the previous month.
PREV_WINDOWS = {"2026-07": ("Avg 16-30 Jun", "2026-06-16", "2026-06-30")}

PALETTE = ["#E91E8C", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA", "#00897B", "#D81B60",
           "#3949AB", "#F4511E", "#6D4C41", "#00ACC1", "#7CB342", "#FFB300", "#5C6BC0", "#EC407A"]


def class_sort_key(cls):
    if cls in CLASS_ORDER:
        return (0, CLASS_ORDER.index(cls), "")
    if cls in CLASS_TAIL:
        return (2, CLASS_TAIL.index(cls), "")
    return (1, 0, cls)


# ── small helpers ───────────────────────────────────────────────────────────
def month_label(ym):
    y, m = map(int, ym.split("-"))
    return f"{calendar.month_abbr[m]} {y}"


def prev_month(ym):
    y, m = map(int, ym.split("-"))
    return (dt.date(y, m, 1) - dt.timedelta(days=1)).strftime("%Y-%m")


def add_days(day, n):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def mmss(seconds):
    s = int(seconds or 0)                # whole seconds, not rounded
    return f"{s // 60}:{s % 60:02d}"


def mean(xs):
    return sum(xs) / len(xs) if xs else None


# ── spikes ──────────────────────────────────────────────────────────────────
def threshold(value):
    """value >= 100 -> +15 %, 20-99 -> +25 %, < 20 -> +100 %."""
    return 15 if value >= 100 else 25 if value >= 20 else 100


def spike_pct(value, ref):
    """Rise over ref in % when it crosses the threshold for `value`, else None."""
    if not ref or ref <= 0:
        return None
    pct = (value - ref) / ref * 100
    return pct if pct >= threshold(value) else None


class Model:
    """Everything the page shows, derived from the store."""

    def __init__(self, store):
        self.s = store
        self.today = store["today"]
        self.days = sorted(store["facts"])
        self.base_ym = store["base"]["ym"]
        self.months = sorted({d[:7] for d in self.days})        # full months, oldest first
        self.all_months = [self.base_ym] + self.months
        # (class, code) -> {date: n}; code "" is the class total
        self.series = {}
        for day, classes in store["facts"].items():
            for cls, codes in classes.items():
                self.series.setdefault((cls, ""), {})[day] = sum(codes.values())
                if cls in FLAT_CLASSES:
                    continue
                for code, n in codes.items():
                    if code:
                        self.series.setdefault((cls, code), {})[day] = n

    # ── averages ────────────────────────────────────────────────────────────
    def days_of(self, ym, include_today=False):
        return [d for d in self.days if d[:7] == ym and (include_today or d != self.today)]

    def avg(self, key, days, present_only=False):
        ser = self.series.get(key, {})
        vals = [ser.get(d, 0) for d in days]
        if present_only:
            vals = [v for v in vals if v]
        return mean(vals) or 0.0

    def base_avg(self, key):
        return self.s["base"]["avg"].get("|".join(key), 0.0)

    def prev_days(self, ym, window=True):
        """Days of the comparison month (or its custom window)."""
        days = self.days_of(prev_month(ym))
        if window and ym in PREV_WINDOWS:
            _, lo, hi = PREV_WINDOWS[ym]
            days = [d for d in days if lo <= d <= hi]
        return days

    def prev_avg(self, ym, key, present_only=False, window=True):
        if prev_month(ym) == self.base_ym:
            return self.base_avg(key)
        return self.avg(key, self.prev_days(ym, window), present_only)

    def total(self, day):
        return sum(sum(c.values()) for c in self.s["facts"].get(day, {}).values())

    def month_total(self, ym):
        return sum(self.total(d) for d in self.days_of(ym, True))

    # ── month tab: category table ───────────────────────────────────────────
    def month_rows(self, ym):
        """[(class, code)] in display order; only rows with calls in the month."""
        days = self.days_of(ym, True)
        tot = {k: sum(v.get(d, 0) for d in days) for k, v in self.series.items()}
        rows = []
        for cls in sorted((k[0] for k, n in tot.items() if not k[1] and n), key=class_sort_key):
            rows.append((cls, ""))
            codes = [k for k, n in tot.items() if k[0] == cls and k[1] and n]
            rows += sorted(codes, key=lambda k: (-tot[k], k[1]))
        return rows

    # ── month tab: metrics tables ───────────────────────────────────────────
    def metric(self, queue, name, days):
        """Aggregate one queue metric over days. Returns a number or None."""
        recs = [self.s[queue].get(d) or {} for d in days]
        if not recs:
            return None
        get = lambda k: [r.get(k) or 0 for r in recs]
        vol = sum(get("vol"))
        if name == "vol":
            return vol / len(recs)
        if name in ("missed", "transferred"):                # volume-weighted
            return 100 * sum(get(name)) / vol if vol else 0.0
        if name == "repeat":                                  # volume-weighted
            den = sum(get("repeat_den"))
            return 100 * sum(get("repeat")) / den if den else None
        if name in ("aht", "agents"):                         # plain mean of daily values
            return mean(get(name))
        if name == "csat":                                    # mean of days that have a score
            return mean([r["csat"] for r in recs if r.get("csat") is not None])
        if name == "low":                                     # mean of daily shares
            return mean([100 * (r.get("low") or 0) / r["vol"] for r in recs if r.get("vol")]) or 0.0
        raise KeyError(name)

    def month_metric(self, queue, name, ym):
        if ym == self.base_ym:
            return self.s["base"][queue].get(name)
        return self.metric(queue, name, self.days_of(ym))

    # ── highlights bar ──────────────────────────────────────────────────────
    def highlights(self, ym):
        """(title, [(is_class, label, value, pct)]): the largest threshold-crossing rises.

        Current month compares its last two complete days, past months their average.
        Class rows average over days that have calls; code rows over all days."""
        days = self.days_of(ym)
        left = f"{month_label(ym)} avg"
        if ym == self.today[:7]:
            days = days[-2:]
            span = "–".join(str(int(d[8:])) for d in days)
            left = f"{span} {month_label(ym)}"
        right = PREV_WINDOWS[ym][0] if ym in PREV_WINDOWS else f"{month_label(prev_month(ym))} avg"
        chips = []
        for key in self.series:
            is_class = not key[1]
            if is_class and prev_month(ym) == self.base_ym and key[0] in self.s["base"].get("sparse", []):
                continue                # its present-days average is not known for the base month
            a = self.avg(key, days, present_only=is_class)
            pct = spike_pct(a, self.prev_avg(ym, key, present_only=is_class))
            if pct is not None:
                chips.append((is_class, key[1] or key[0], a, pct))
        chips.sort(key=lambda c: -c[3])
        return f"Top spikes — {left} vs {right}", chips[:MAX_CHIPS]

    # ── MoM tab ─────────────────────────────────────────────────────────────
    def mom_avg(self, key, ym):
        return self.base_avg(key) if ym == self.base_ym else self.avg(key, self.days_of(ym, True))

    def base_keys(self):
        return [tuple(k.split("|", 1)) for k in self.s["base"]["avg"]]

    def mom_classes(self):
        found = {k[0] for k in self.series} | {k[0] for k in self.base_keys()}
        return sorted(found, key=class_sort_key)

    def mom_cat_series(self):
        return {c: [self.mom_avg((c, ""), m) for m in self.all_months] for c in self.mom_classes()}

    def code_keys(self):
        return {k for k in [*self.series, *self.base_keys()] if k[1]}

    def code_class_map(self):
        return {code: cls for cls, code in sorted(self.code_keys(), key=lambda k: (class_sort_key(k[0]), k[1]))}

    def mom_code_series(self):
        by_code = {}
        for key in self.code_keys():
            by_code.setdefault(key[1], []).append(key)
        ser = {c: [sum(self.mom_avg(k, m) for k in ks) for m in self.all_months] for c, ks in by_code.items()}
        top = sorted(ser, key=lambda c: -sum(ser[c]))[:TOP_CODES]
        return {c: ser[c] for c in top}

    def miss_mom(self, queue):
        out = []
        for ym in self.all_months:
            v, vol = self.month_metric(queue, "missed", ym), self.month_metric(queue, "vol", ym)
            out.append(round(v, 1) if v is not None and vol else None)
        return out

    def csat_mom(self, queue):
        return [self.s["months"].get(ym, {}).get("csat_" + queue) for ym in self.all_months]

    # ── header stats ────────────────────────────────────────────────────────
    def per_tab(self):
        totals, codes, miss, aht, paying, active = {}, {}, {}, {}, {}, {}
        for ym in self.months:
            tab, info = "tab-" + ym, self.s["months"].get(ym, {})
            totals[tab] = self.month_total(ym)
            codes[tab] = sum(1 for k in self.month_rows(ym) if k[1])
            miss[tab] = round(self.month_metric("svc", "missed", ym) or 0, 1)
            aht[tab] = mmss(self.month_metric("svc", "aht", ym))
            paying[tab], active[tab] = info.get("paying"), info.get("active")
        last = "tab-" + self.months[-1]
        totals["tab-mom"] = sum(totals.values())
        codes["tab-mom"] = len(self.code_class_map())
        miss["tab-mom"] = round(mean(list(miss.values())), 1)
        aht["tab-mom"] = mmss(mean([self.month_metric("svc", "aht", ym) or 0 for ym in self.all_months]))
        totals["tab-current"] = self.total(self.today)
        miss["tab-current"] = round(self.metric("svc", "missed", [self.today]), 1)
        aht["tab-current"] = ""
        for d in (paying, active):
            d["tab-mom"] = d["tab-current"] = d["tab-callback"] = d[last]
        drop_none = lambda d: {k: v for k, v in d.items() if v is not None}
        return totals, codes, miss, aht, drop_none(paying), drop_none(active)

    # ── Current tab ─────────────────────────────────────────────────────────
    def current_dates(self):
        days = [add_days(self.today, -i) for i in range(HOURLY_DAYS)]
        return [d for d in days if d in self.s["hourly"] or d == self.today]

    def _done(self, day, hour):
        return day != self.today or hour < self.s["now_hour"]

    def _window(self, day, window):
        """Up to seven loaded dates before `day`, newest first."""
        return [p for p in (add_days(day, -i) for i in range(1, 8)) if p in window]

    def current_payload(self):
        """Service queue by hour: t=calls, a=7-day average, ct/ac by class, co/ao by code."""
        H = self.s["hourly"]
        win = sorted(self.current_dates())
        cell = lambda d, h: H.get(d, {}).get(str(h), {})
        all_t = {d: {str(h): (sum(sum(c.values()) for c in cell(d, h).values()) if self._done(d, h) else None)
                     for h in HOURS} for d in win}
        payload = {}
        for d in win:
            past = self._window(d, win)
            n = len(past)
            item = {"t": all_t[d], "a": {}, "p": past, "ct": {}, "ac": {}, "co": {}, "ao": {}, "isd": {}}
            for hour in HOURS:
                h = str(hour)
                now = cell(d, h) if self._done(d, hour) else {}
                item["a"][h] = round(sum(all_t[p][h] or 0 for p in past) / n, 1) if n else 0.0
                item["ct"][h] = {c: sum(v.values()) for c, v in now.items()}
                item["co"][h] = now
                ac, ao = Counter(), {}
                for p in past:
                    for c, v in cell(p, h).items():
                        ac[c] += sum(v.values())
                        ao.setdefault(c, Counter()).update(v)
                item["ac"][h] = {c: round(v / n, 1) for c, v in ac.items()}
                item["ao"][h] = {c: {k: round(v / n, 1) for k, v in cs.items()} for c, cs in ao.items()}
                item["isd"][h] = self.s["isd"].get(d, {}).get(h, {}) if self._done(d, hour) else {}
            payload[d] = item
        return payload, all_t

    def isd_daily(self):
        """Roll the hourly top-5 CSP / top-3 ISP lists up to the day."""
        out = {}
        for d in sorted(self.current_dates()):
            csp, isp = Counter(), Counter()
            for hour in HOURS:
                h = self.s["isd"].get(d, {}).get(str(hour), {})
                for k, v in h.get("csp", []):
                    csp[k] += v
                for k, v in h.get("isp", []):
                    isp[k] += v
            out[d] = {"total": sum(csp.values()),
                      "csp": [list(x) for x in csp.most_common(5)],
                      "isp": [list(x) for x in isp.most_common(3)]}
        return out

    def wn_payload(self):
        W = self.s["wn_hourly"]
        win = [d for d in sorted(self.current_dates()) if W.get(d)]
        all_t = {d: {str(h): (sum(W[d].get(str(h), {}).values()) if self._done(d, h) else None)
                     for h in WN_HOURS} for d in win}
        payload = {}
        for d in win:
            past = self._window(d, win)
            n = len(past)
            item = {"h": WN_HOURS, "t": all_t[d], "a": {}, "p": past, "ct": {}, "ac": {}}
            for hour in WN_HOURS:
                h = str(hour)
                item["a"][h] = round(sum(all_t[p][h] or 0 for p in past) / n, 1) if n else 0.0
                item["ct"][h] = W[d].get(h, {}) if self._done(d, hour) else {}
                ac = Counter()
                for p in past:
                    ac.update(W[p].get(h, {}))
                item["ac"][h] = {c: round(v / n, 1) for c, v in ac.items()}
            payload[d] = item
        return payload, all_t, {d: self.s["wn_mw"][d] for d in win if d in self.s["wn_mw"]}
