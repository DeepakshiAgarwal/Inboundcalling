"""Callback Calls tab: auto-dialled calls of the wiom_talktime_threshold_outbound campaign.

store["callback"] = {date: {dials, customers, connected, reached, no_answer, hangup, agents, aht,
                            cats: {class: {code: n}}, hours: {hour: [dials, connected]}}}

One dial = one attempt. Connected = SYSTEM_DISPOSITION 'CONNECTED'. Reached = customers with at least one
connected dial that day. Days without dials are left out of averages (the campaign does not run every day)."""
import datetime as dt
import html
import json

from logic import FLAT_CLASSES, class_sort_key, mean, mmss, month_label, prev_month, spike_pct

CAMPAIGN = "wiom_talktime_threshold_outbound"
START = "2026-07-01"

# label, key, how a period is summarised
METRICS = [("Callback Dials", "dials"), ("Customers Dialled", "customers"), ("Attempts per Customer", "attempts"),
           ("Connected Calls", "connected"), ("Connect % (of dials)", "connect_pct"),
           ("Customers Reached %", "reach_pct"), ("No Answer %", "no_answer_pct"),
           ("Hung Up by Customer %", "hangup_pct"), ("AHT (Avg Handle Time)", "aht"), ("Agents on Callbacks", "agents")]
RATIOS = {"attempts": ("dials", "customers"), "connect_pct": ("connected", "dials"),
          "reach_pct": ("reached", "customers"), "no_answer_pct": ("no_answer", "dials"),
          "hangup_pct": ("hangup", "dials")}


# ── SQL ─────────────────────────────────────────────────────────────────────
def where(table, lo, hi):
    return (f"FROM {table} WHERE CAMPAIGN_NAME = '{CAMPAIGN}' AND CALL_TYPE = 'outbound.auto.dial' "
            f"AND CALL_TIME >= DATE '{lo}' AND CALL_TIME::DATE <= DATE '{hi}'")


def sql_daily(table, lo, hi, talk, handle):
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, COUNT(*) AS DIALS,
       COUNT(DISTINCT PHONE) AS CUSTOMERS, COUNT_IF(SYSTEM_DISPOSITION = 'CONNECTED') AS CONNECTED,
       COUNT(DISTINCT IFF(SYSTEM_DISPOSITION = 'CONNECTED', PHONE, NULL)) AS REACHED,
       COUNT_IF(SYSTEM_DISPOSITION = 'NO_ANSWER') AS NO_ANSWER,
       COUNT_IF(SYSTEM_DISPOSITION = 'CALL_HANGUP') AS HANGUP,
       COUNT(DISTINCT IFF(SYSTEM_DISPOSITION = 'CONNECTED', NULLIF(TRIM(USER_NAME), ''), NULL)) AS AGENTS,
       AVG(IFF(SYSTEM_DISPOSITION = 'CONNECTED' AND {talk} > 0, {handle}, NULL)) AS AHT
{where(table, lo, hi)} GROUP BY 1 /* callback daily */"""


def sql_hours(table, lo, hi):
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, HOUR(CALL_TIME) AS H, COUNT(*) AS N,
       COUNT_IF(SYSTEM_DISPOSITION = 'CONNECTED') AS C
{where(table, lo, hi)} GROUP BY 1, 2 /* callback hours */"""


def sql_cats(table, lo, hi):
    """Categories describe connected calls only."""
    return f"""SELECT TO_VARCHAR(CALL_TIME::DATE, 'YYYY-MM-DD') AS D, TRIM(DISPOSITION_CLASS) AS CLS,
       TRIM(DISPOSITION_CODE) AS CODE, COUNT(*) AS N
{where(table, lo, hi)} AND SYSTEM_DISPOSITION = 'CONNECTED' GROUP BY 1, 2, 3 /* callback cats */"""


def from_rows(daily, hours, cats, roll_up):
    out = {}
    for r in daily:
        rec = {k: int(r[k.upper()] or 0) for k in
               ("dials", "customers", "connected", "reached", "no_answer", "hangup", "agents")}
        out[r["D"]] = {**rec, "aht": float(r["AHT"] or 0), "cats": {}, "hours": {}}
    for r in hours:
        if r["D"] in out:
            out[r["D"]]["hours"][str(int(r["H"]))] = [int(r["N"]), int(r["C"])]
    for r in cats:
        if r["D"] in out:
            cls = roll_up(r["CLS"] or "") or "No disposition"
            code = "" if cls in FLAT_CLASSES else (r["CODE"] or "")
            codes = out[r["D"]]["cats"].setdefault(cls, {})
            codes[code] = codes.get(code, 0) + int(r["N"])
    return out


# ── numbers ─────────────────────────────────────────────────────────────────
class Callback:
    def __init__(self, store):
        self.data, self.today = store.get("callback") or {}, store["today"]
        self.days = sorted(d for d in self.data if self.data[d]["dials"])
        self.months = sorted({d[:7] for d in self.days})

    def days_of(self, ym, with_today=False):
        return [d for d in self.days if d[:7] == ym and (with_today or d != self.today)]

    def metric(self, name, days):
        recs = [self.data[d] for d in days]
        if not recs:
            return None
        if name in RATIOS:
            top, bottom = (sum(r[k] for r in recs) for k in RATIOS[name])
            return (top / bottom) * (1 if name == "attempts" else 100) if bottom else None
        if name == "aht":                       # weighted by connected calls
            n = sum(r["connected"] for r in recs)
            return sum(r["aht"] * r["connected"] for r in recs) / n if n else None
        return mean([r[name] for r in recs])

    def count(self, day, cls, code):
        codes = self.data[day]["cats"].get(cls, {})
        return codes.get(code, 0) if code else sum(codes.values())

    def avg(self, cls, code, days):
        return mean([self.count(d, cls, code) for d in days]) or 0.0

    def rows(self, ym):
        days, tot = self.days_of(ym, True), {}
        for d in days:
            for cls, codes in self.data[d]["cats"].items():
                for code, n in codes.items():
                    tot.setdefault(cls, {}).setdefault(code, 0)
                    tot[cls][code] += n
        out = []
        for cls in sorted(tot, key=class_sort_key):
            out.append((cls, ""))
            out += [(cls, c) for c in sorted(filter(None, tot[cls]), key=lambda c: (-tot[cls][c], c))]
        return out


# ── HTML ────────────────────────────────────────────────────────────────────
def show(name, v):
    if v is None:
        return "—"
    if name == "aht":
        return mmss(v)
    if name == "attempts":
        return f"{v:.1f}"
    return f"{v:.1f}%" if name in RATIOS else f"{v:.0f}"


def spike(pct):
    return "" if pct is None else f'<span class="spike">(+{pct:.0f}%) &uarr;</span>'


def slug(cls):
    keep = "".join(c if c.isalnum() else "_" for c in cls)
    return "cb_" + keep


def month_block(cb, ym):
    cur, pm = cb.days_of(ym), prev_month(ym)
    prev, days = cb.days_of(pm), cb.days_of(ym, True)[::-1]
    label = lambda d: dt.date.fromisoformat(d).strftime("%d-%b")
    heads = [f"{month_label(ym)} Avg (MTD)", f"{month_label(pm)} Avg", *map(label, days)]
    th = "".join(f"<th>{h}</th>" for h in heads)

    rows = []
    for text, name in METRICS:
        tds = f'<td class="num mtd avgcol">{show(name, cb.metric(name, cur))}</td>'
        tds += f'<td class="num prev avgcol">{show(name, cb.metric(name, prev))}</td>'
        tds += "".join(f'<td class="num">{show(name, cb.metric(name, [d]))}</td>' for d in days)
        rows.append(f'<tr class="row-class"><td class="disp disp-class">{text}</td>{tds}</tr>')
    metrics = (f'<table class="dash metrics-dash"><tr><td colspan="{len(heads) + 1}" style="background:#1a4b2e;'
               f'color:#fff;font-weight:500;padding:6px 10px;font-size:13px;">Callback Campaign &mdash; days it ran</td></tr>'
               f'<thead><tr><th>Metrics</th>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>')

    body, keys = [], cb.rows(ym)
    parents = {c for c, code in keys if code}
    for cls, code in keys:
        m, p = cb.avg(cls, code, cur), cb.avg(cls, code, prev)
        if code:
            body.append(f'<tr class="row-code code-of-{slug(cls)}"><td class="disp disp-code">{html.escape(code)}</td>')
        elif cls in parents:
            body.append(f'<tr class="row-class"><td class="disp disp-class" data-toggle="{slug(cls)}">'
                        f'<span class="toggle-icon">&#9660;</span> {html.escape(cls)}</td>')
        else:
            body.append(f'<tr class="row-class"><td class="disp disp-class">{html.escape(cls)}</td>')
        body.append(f'<td class="num mtd avgcol">{m:.0f}{spike(spike_pct(m, p))}</td><td class="num prev avgcol">{p:.0f}</td>')
        for d in days:
            n = cb.count(d, cls, code)
            body.append(f'<td class="num">{n}{spike(spike_pct(n, m))}</td>')
        body.append("</tr>")
    total = lambda d: cb.data[d]["connected"]
    m, p = mean([total(d) for d in cur]) or 0.0, mean([total(d) for d in prev]) or 0.0
    body.append(f'<tr class="row-total"><td class="disp total-label">TOTAL connected</td><td class="num mtd avgcol">{m:.0f}'
                f'{spike(spike_pct(m, p))}</td><td class="num prev avgcol">{p:.0f}</td>'
                + "".join(f'<td class="num">{total(d)}{spike(spike_pct(total(d), m))}</td>' for d in days) + "</tr>")
    cats = (f'<table class="dash"><thead><tr><th>Connected callbacks by category</th>{th}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')

    hours = sorted({int(h) for d in days for h in cb.data[d]["hours"]})
    base = cur or days
    trs = []
    for h in hours:
        got = [cb.data[d]["hours"].get(str(h), [0, 0]) for d in base]
        dials, conn = sum(x[0] for x in got), sum(x[1] for x in got)
        rate = f"{100 * conn / dials:.1f}%" if dials else "—"
        trs.append(f'<tr><td class="curr-hour-cell">{h:02d}:00–{h + 1:02d}:00</td><td class="curr-avg-cell">'
                   f'{dials / len(base):.0f}</td><td>{conn / len(base):.1f}</td><td>{rate}</td></tr>')
    hourly = ('<h3 style="margin:24px 0 8px;font-size:14px;font-weight:600;color:#1F3864;">By hour &mdash; average per day</h3>'
              '<table class="curr-table" style="max-width:640px"><thead><tr><th style="text-align:left">Hour (IST)</th>'
              f'<th>Dials</th><th>Connected</th><th>Connect %</th></tr></thead><tbody>{"".join(trs)}</tbody></table>')
    return (f'<div class="cb-month" data-ym="{ym}" style="display:none">{metrics}<div style="margin-top:16px"></div>'
            f'{cats}{hourly}</div>')


def section(store):
    """(tab section html, per-month header numbers for the page script)"""
    cb = Callback(store)
    if not cb.days:
        return ('<section id="tab-callback" class="tab-panel"><p style="color:#888;padding:24px 0">No callback data yet. '
                'It appears after the next refresh.</p></section>'), {}
    months = cb.months[::-1]
    stats, chart = {}, {}
    for ym in months:
        days = cb.days_of(ym, True)
        stats[ym] = {"dials": sum(cb.data[d]["dials"] for d in days), "connect": show("connect_pct", cb.metric("connect_pct", days)),
                     "aht": mmss(cb.metric("aht", days)), "label": month_label(ym)}
        chart[ym] = {"labels": [d[8:] + "-" + dt.date.fromisoformat(d).strftime("%b") for d in days],
                     "connected": [cb.data[d]["connected"] for d in days],
                     "other": [cb.data[d]["dials"] - cb.data[d]["connected"] for d in days],
                     "reach": [round(cb.metric("reach_pct", [d]) or 0, 1) for d in days]}
        cards = [("Callback dials", f"{stats[ym]['dials']:,}"),
                 ("Customers reached", show("reach_pct", cb.metric("reach_pct", days))),
                 ("Connect % (of dials)", stats[ym]["connect"]),
                 ("Attempts per customer", show("attempts", cb.metric("attempts", days))), ("AHT", stats[ym]["aht"]),
                 ("Days it ran", str(len(days)))]
        stats[ym]["cards"] = cards
    options = "".join(f'<option value="{ym}">{month_label(ym)}</option>' for ym in months)
    return (f'''<section id="tab-callback" class="tab-panel">
<div class="curr-date-filter">
  <label for="cbMonth" style="font-weight:600;margin-right:8px">&#128222; Callback month:</label>
  <select id="cbMonth" onchange="renderCallback()" style="padding:6px 14px;border-radius:6px;border:1.5px solid #e91e8c;font-size:14px;cursor:pointer;background:#fff;color:#222">{options}</select>
  <span style="color:#666;font-size:11.5px">Auto-dialled campaign {CAMPAIGN}. One customer can be dialled several times a day.</span>
</div>
<div id="cb-banner" class="curr-banner"></div>
<div class="chart-wrap" style="position:relative;height:340px;width:100%;overflow:hidden;margin-bottom:24px;"><canvas id="cbChart"></canvas></div>
{"".join(month_block(cb, ym) for ym in months)}
<script>
var CB_STATS = {json.dumps(stats)};
var CB_CHART = {json.dumps(chart)};
var _cbChart = null;
function renderCallback() {{
  var ym = document.getElementById('cbMonth').value, c = CB_CHART[ym];
  document.querySelectorAll('.cb-month').forEach(function (el) {{ el.style.display = el.dataset.ym === ym ? '' : 'none'; }});
  document.getElementById('cb-banner').innerHTML = CB_STATS[ym].cards.map(function (k) {{
    return '<div class="stat-card"><div class="k">' + k[0] + '</div><div class="v">' + k[1] + '</div></div>'; }}).join('');
  document.getElementById('stat-total-label').textContent = 'Callback Dials (' + CB_STATS[ym].label + ')';
  document.getElementById('stat-total').textContent = CB_STATS[ym].dials.toLocaleString('en-IN');
  document.getElementById('stat-miss-label').textContent = 'Connect % (of dials)';
  document.getElementById('stat-miss').textContent = CB_STATS[ym].connect;
  document.getElementById('stat-aht').textContent = CB_STATS[ym].aht;
  if (_cbChart) _cbChart.destroy();
  _cbChart = new Chart(document.getElementById('cbChart').getContext('2d'), {{
    data: {{ labels: c.labels, datasets: [
      {{ type: 'bar', label: 'Connected', data: c.connected, backgroundColor: '#E91E8C', stack: 'd', order: 2 }},
      {{ type: 'bar', label: 'Not connected', data: c.other, backgroundColor: '#D0D0D0', stack: 'd', order: 2 }},
      {{ type: 'line', label: 'Customers reached %', data: c.reach, yAxisID: 'pct', borderColor: '#1F3864',
         backgroundColor: '#1F3864', borderWidth: 2, pointRadius: 3, tension: 0.3, order: 1 }} ] }},
    options: {{ responsive: true, maintainAspectRatio: false, interaction: {{ mode: 'index', intersect: false }},
      plugins: {{ title: {{ display: true, text: 'Callback dials per day and customers reached', font: {{ size: 14, weight: 'bold' }} }},
        legend: {{ position: 'top', labels: {{ boxWidth: 14, font: {{ size: 12 }} }} }} }},
      scales: {{ x: {{ stacked: true, grid: {{ display: false }} }},
        y: {{ stacked: true, beginAtZero: true, title: {{ display: true, text: 'Dials / day' }} }},
        pct: {{ position: 'right', min: 0, max: 100, grid: {{ drawOnChartArea: false }},
          ticks: {{ callback: function (v) {{ return v + '%'; }} }}, title: {{ display: true, text: 'Customers reached' }} }} }} }}
  }});
}}
</script></section>''', stats)
