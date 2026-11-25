"""Builds the GitHub Pages version into site/ (aggregates only: no drill-through, no phone numbers).

    python build_static.py            pull the last days from Metabase (all history the first time), then build
    python build_static.py --full     re-read all history
    python build_static.py --saved    build from the saved data, no Metabase
"""
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

import pipeline
from logic import add_days
from render import render

ROOT = Path(__file__).parent
SITE = ROOT / "site"

# Open pages reload themselves within a minute of a new version being published.
AUTO_RELOAD = """<script>
(function () {
  var shown = "%s";
  setInterval(function () {
    if (document.hidden) return;
    fetch('version.txt?' + Date.now(), {cache: 'no-store'})
      .then(function (r) { return r.ok ? r.text() : shown; })
      .then(function (v) { if (v.trim() !== shown) location.reload(); }).catch(function () {});
  }, 60000);
})();
</script>
</body>"""


def swap(page, old, new):
    if page.count(old) != 1:
        raise RuntimeError(f"template changed: expected one {old[:50]!r}, found {page.count(old)}")
    return page.replace(old, new)


def to_static(page, version):
    """Remove everything that needs a server."""
    page = page.replace(' title="Double-click for raw data"', "")
    page = swap(page, "\n    &middot; Double-click any number for raw call records.", "")
    page = swap(page, "  td.addEventListener('dblclick', () => openDrill(td));\n", "")
    page = re.sub(r"function openDrill\(td\) \{.*?(?=// ---- Wiom Net Calls vs Plans Sold ----)", "", page, flags=re.S)
    page = re.sub(r"function manualRefresh\(btn\) \{.*?(?=const PER_TAB_TOTALS)",
                  "function manualRefresh() { location.reload(); }\n", page, flags=re.S)
    page = swap(page, ">Refresh Now</button>", ">Reload</button>")
    page = swap(page, "auto-refreshed hourly 08:00–22:00 IST (incl. same-day live data) · "
                      'Use "Refresh Now" for instant update', "auto-refreshed 08:00–22:00 IST (incl. same-day live data)")
    page = swap(page, "</head>", '<meta name="robots" content="noindex">\n</head>')
    page = swap(page, "</body>", AUTO_RELOAD % html.escape(version))
    for banned in ("'/raw", "'/refresh", "dblclick"):
        if banned in page:
            raise RuntimeError(f"server-only code left in the page: {banned}")
    if re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", page):
        raise RuntimeError("the page contains something that looks like a phone number; not publishing")
    return page


def main():
    saved = ROOT / "data" / "store.json"
    first_time = not saved.exists()
    store = json.loads((ROOT / "data" / "snapshot.json" if first_time else saved).read_text(encoding="utf-8"))
    if "--saved" not in sys.argv:
        store = pipeline.refresh(store, full=first_time or "--full" in sys.argv)
        yesterday = add_days(store["today"], -1)
        if not (store["svc"].get(yesterday) or {}).get("vol"):
            raise SystemExit(f"No Service calls found for {yesterday}; keeping the published page")
    page = to_static(render(store), store["as_of"])
    saved.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(page, encoding="utf-8", newline="\n")
    (SITE / "version.txt").write_text(store["as_of"], encoding="utf-8")
    (SITE / ".nojekyll").touch()
    print(f"built {store['as_of']}: {len(page) / 1e6:.1f} MB at {dt.datetime.now(pipeline.IST):%H:%M IST}")


if __name__ == "__main__":
    main()
