"""Turns a store into the dashboard HTML (templates/page.html with @@TOKENS@@)."""
import datetime as dt
import html
import json
from pathlib import Path

from logic import PALETTE, PREV_WINDOWS, Model, mean, mmss, month_label, prev_month, spike_pct

TEMPLATE = Path(__file__).with_name("templates") / "page.html"
METRICS = [("Call Volume", "vol"), ("Missed Call %", "missed"), ("AHT (Avg Handle Time)", "aht"),
           ("Agents Logged", "agents"), ("Transferred % (to AQ)", "transferred"), ("CSAT %", "csat"),
           ("Repeat Caller %", "repeat"), ("Low Pain %", "low")]
GAP = '<div style="margin-top:16px"></div>'


def esc(s):
    return html.escape(str(s))


def compact(o):
    return json.dumps(o, separators=(",", ":")).replace("</", "<\\/")


def spaced(o):
    return json.dumps(o).replace("</", "<\\/")


def fmt_metric(name, v):
    if v is None:
        return "—"
    if name in ("vol", "agents"):
        return f"{v:.0f}"
    return mmss(v) if name == "aht" else f"{v:.1f}%"


def spike_html(pct):
    return "" if pct is None else f'<span class="spike">(+{pct:.0f}%) &uarr;</span>'


def slug(cls):
    return esc(cls.replace(" ", "_").replace("(", "").replace(")", ""))


def day_label(d):
    return dt.date.fromisoformat(d).strftime("%d-%b")


# ── month tabs ──────────────────────────────────────────────────────────────
def metrics_table(m, ym, title, queue, names, heads):
    pm, win = prev_month(ym), PREV_WINDOWS.get(ym)
    faded = ' style="opacity:.8"' if win else ""
    rows = []
    for label, name in METRICS:
        if name not in names:
            continue
        show = lambda v: fmt_metric(name, v)
        tds = f'<td class="num mtd avgcol">{show(m.month_metric(queue, name, ym))}</td>'
        tds += f'<td class="num prev avgcol"{faded}>{show(m.month_metric(queue, name, pm))}</td>'
        if win:
            tds += f'<td class="num prev avgcol">{show(m.metric(queue, name, m.prev_days(ym)))}</td>'
        tds += "".join(f'<td class="num">{show(m.metric(queue, name, [d]))}</td>' for d in m.days_of(ym, True)[::-1])
        rows.append(f'<tr class="row-class"><td class="disp disp-class">{label}</td>{tds}</tr>')
    return (f'<table class="dash metrics-dash"><tr><td colspan="{len(heads) + 1}" style="background:#1a4b2e;color:#fff;'
            f'font-weight:500;padding:6px 10px;font-size:13px;">{title}</td></tr><thead><tr><th>Metrics</th>'
            + "".join(f"<th>{h}</th>" for h in heads) + f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def category_table(m, ym, heads):
    pm, win = prev_month(ym), PREV_WINDOWS.get(ym)
    days, mtd_days = m.days_of(ym, True)[::-1], m.days_of(ym)

    def td(css, key, scope, date, dym, value, pct=None):
        return (f'<td class="{css}" data-cls="{esc(key[0])}" data-code="{esc(key[1])}" data-scope="{scope}" '
                f'data-date="{date}" data-ym="{dym}" title="Double-click for raw data">'
                f'{value:.0f}{spike_html(pct)}</td>')

    out = ['<table class="dash"><thead><tr><th>Category</th>', *(f"<th>{h}</th>" for h in heads), "</tr></thead><tbody>"]
    rows = m.month_rows(ym)
    parents = {cls for cls, code in rows if code}
    for key in rows:
        cls, code = key
        ser, mtd, prev = m.series[key], m.avg(key, mtd_days), m.prev_avg(ym, key)
        if code:
            out.append(f'<tr class="row-code code-of-{slug(cls)}"><td class="disp disp-code">{esc(code)}</td>')
        elif cls in parents:
            out.append(f'<tr class="row-class"><td class="disp disp-class" data-toggle="{slug(cls)}">'
                       f'<span class="toggle-icon">&#9660;</span> {esc(cls)}</td>')
        else:
            out.append(f'<tr class="row-class"><td class="disp disp-class">{esc(cls)}</td>')
        out.append(td("num mtd avgcol", key, "mtd", "", ym, mtd, spike_pct(mtd, prev)))
        if win:
            out.append(f'<td class="num prev avgcol" style="opacity:.8">{m.prev_avg(ym, key, window=False):.0f}</td>')
        out.append(td("num prev avgcol", key, "prev", "", pm, prev))
        for d in days:
            v = ser.get(d, 0)
            out.append(td("num", key, "day", d, ym, v, spike_pct(v, mtd)))
        out.append("</tr>")

    avg_total = lambda ds: mean([m.total(d) for d in ds]) or 0.0
    mtd = avg_total(mtd_days)
    full_prev = m.s["base"]["total"] if pm == m.base_ym else avg_total(m.days_of(pm))
    prev = avg_total(m.prev_days(ym)) if win else full_prev
    out.append(f'<tr class="row-total"><td class="disp total-label">TOTAL</td>'
               f'<td class="num mtd avgcol">{mtd:.0f}{spike_html(spike_pct(mtd, prev))}</td>')
    if win:
        out.append(f'<td class="num prev avgcol" style="opacity:.8">{full_prev:.0f}</td>')
    out.append(f'<td class="num prev avgcol">{prev:.0f}</td>')
    out += [f'<td class="num">{m.total(d)}{spike_html(spike_pct(m.total(d), mtd))}</td>' for d in days]
    out.append("</tr></tbody></table>")
    return "".join(out)


def month_section(m, ym):
    win = PREV_WINDOWS.get(ym)
    heads = [f"{month_label(ym)} Avg (MTD)", f"{month_label(prev_month(ym))} Avg"]
    heads += ([win[0]] if win else []) + [day_label(d) for d in m.days_of(ym, True)[::-1]]
    every = [n for _, n in METRICS]
    return (f'<section id="tab-{ym}" class="tab-panel">'
            + metrics_table(m, ym, "Service Queue (High Pain + Low Pain)", "svc", every, heads) + GAP
            + metrics_table(m, ym, "Wiom Net Queue", "wn", every[:-1], heads) + GAP
            + category_table(m, ym, heads) + "</section>")


def highlights_bar(m, ym):
    title, chips = m.highlights(ym)
    if not chips:
        return ""
    body = "".join(
        f'<span class="hl-chip" style="font-weight:{700 if is_cls else 400}">{esc(label)} '
        f'<span class="hl-val">{v:.0f}</span><span class="hl-pct">(+{pct:.0f}%) &uarr;</span></span>'
        for is_cls, label, v, pct in chips)
    return (f'<div class="highlights hl-tab" data-tab="tab-{ym}"><div class="hl-title">{title}</div>'
            f'<div class="hl-chips">{body}</div></div>')


# ── MoM tab ─────────────────────────────────────────────────────────────────
def pct_table(m, label, svc, wn):
    th = "".join(f"<th>{month_label(x)}</th>" for x in m.all_months)

    def row(name, vals):
        tds = "".join(f'<td class="num">{"—" if v is None else f"{v:.1f}%"}</td>' for v in vals)
        return f'<tr class="row-class"><td class="disp disp-class">{name}</td>{tds}</tr>'

    return (f'<table class="dash metrics-dash"><thead><tr><th>Queue</th>{th}</tr></thead><tbody>'
            f'{row("Service Queue " + label, svc)}{row("Wiom Net Queue " + label, wn)}</tbody></table>')


def mom_tables(m, cats, codes):
    th = "".join(f"<th>{month_label(x)} Avg</th>" for x in m.all_months)

    def nums(vals, css="num"):
        return "".join(f'<td class="{css}">{v:.0f}</td>' for v in vals)

    body = "".join(f'<tr class="row-class"><td class="disp disp-class">{esc(c)}</td>{nums(v)}</tr>'
                   for c, v in cats.items())
    totals = [sum(round(v[i]) for v in cats.values()) for i in range(len(m.all_months))]
    body += f'<tr class="row-total"><td class="disp total-label">TOTAL (all classes)</td>{nums(totals, "num mtd")}</tr>'
    cat = f'<table class="dash"><thead><tr><th>Disposition Class</th>{th}</tr></thead><tbody>{body}</tbody></table>'
    body = "".join(f'<tr class="row-code"><td class="disp disp-code" style="padding-left:10px">{esc(c)}</td>'
                   f'{nums(v)}</tr>' for c, v in codes.items())
    code = f'<table class="dash"><thead><tr><th>Sub-category Issue</th>{th}</tr></thead><tbody>{body}</tbody></table>'
    return cat, code


# ── page ────────────────────────────────────────────────────────────────────
def date_option(m, d):
    x = dt.date.fromisoformat(d)
    if d == m.today:
        return f'<option value="{d}" selected>Today ({x.strftime("%d %b %Y")})</option>'
    return f'<option value="{d}">{x.strftime("%d %b %Y")} ({x.strftime("%a")})</option>'


def tab_button(target, label):
    return f'<button class="tab-btn" data-target="{target}">{label}</button>'


def colors(keys):
    return {k: PALETTE[i % len(PALETTE)] for i, k in enumerate(keys)}


def one_dp(series):
    return {k: [round(x, 1) for x in v] for k, v in series.items()}


def render(store):
    m = Model(store)
    months = m.months[::-1]
    payload, all_t = m.current_payload()
    wn_payload, wn_all_t, wn_mw = m.wn_payload()
    cats, codes = m.mom_cat_series(), m.mom_code_series()
    cat_table, code_table = mom_tables(m, cats, codes)
    totals, code_counts, miss, aht, paying, active = m.per_tab()
    svc_miss, wn_miss = m.miss_mom("svc"), m.miss_mom("wn")
    svc_csat, wn_csat = m.csat_mom("svc"), m.csat_mom("wn")
    tokens = {
        "N_MONTHS": len(m.all_months), "N_CATS": len(cats), "N_CODES": code_counts["tab-mom"],
        "AS_OF": esc(store["as_of"]),
        "HIGHLIGHTS": "\n".join(filter(None, (highlights_bar(m, ym) for ym in m.months))),
        "TABS": tab_button("tab-current", "&#9679; Current")
                + "".join(tab_button("tab-" + ym, month_label(ym)) for ym in months)
                + tab_button("tab-mom", "MoM Comparison"),
        "DATE_OPTIONS": "\n    ".join(date_option(m, d) for d in m.current_dates()),
        "TODAY": m.today,
        "PAYLOAD": compact(payload), "ALL_T": compact(all_t), "ISD_DAILY": compact(m.isd_daily()),
        "WN_PAYLOAD": compact(wn_payload), "WN_ALL_T": compact(wn_all_t), "WN_MW": compact(wn_mw),
        "MONTH_SECTIONS": "".join(month_section(m, ym) for ym in months),
        "MISS_TABLE": pct_table(m, "Missed %", svc_miss, wn_miss),
        "CSAT_TABLE": pct_table(m, "CSAT %", svc_csat, wn_csat),
        "CLASS_OPTIONS": "\n      ".join(f'<option value="{esc(c)}">{esc(c)}</option>' for c in cats),
        "CAT_TABLE": cat_table, "CODE_TABLE": code_table,
        "PER_TAB_TOTALS": spaced(totals), "CODES_PER_TAB": spaced(code_counts), "PER_TAB_MISSPCT": spaced(miss),
        "PER_TAB_AHT": spaced(aht), "PER_TAB_PAYING": spaced(paying), "PER_TAB_ACTIVE": spaced(active),
        "WIOM_NET_PLANS": spaced(store["plans"]),
        "MOM_LABELS": spaced([month_label(x) for x in m.all_months]),
        "CAT_SERIES": spaced(one_dp(cats)), "CAT_COLORS": spaced(colors(cats)),
        "CODE_SERIES": spaced(one_dp(codes)), "CODE_COLORS": spaced(colors(codes)), "CODE_MAP": spaced(m.code_class_map()),
        "SVC_MISS": spaced(svc_miss), "WN_MISS": spaced(wn_miss),
        "SVC_CSAT": spaced(svc_csat), "WN_CSAT": spaced(wn_csat),
    }
    parts = TEMPLATE.read_text(encoding="utf-8").split("@@")      # text, TOKEN, text, TOKEN, ...
    return "".join(str(tokens[p]) if i % 2 else p for i, p in enumerate(parts))
