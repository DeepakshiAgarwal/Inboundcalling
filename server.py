"""Dashboard web server (standard library only).

  python server.py            serve on http://localhost:8095
  python server.py --refresh  pull from Metabase once, then exit
  python server.py --full     same, re-reading all history

Routes: /  /refresh  /raw  /version  /healthz
"""
import base64
import datetime as dt
import gzip
import hmac
import html
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import metabase
import pipeline
from render import render

DATA = Path(metabase.env("DATA_DIR") or Path(__file__).with_name("data"))
SNAPSHOT = Path(__file__).with_name("data") / "snapshot.json"
STORE = DATA / "store.json"
HOSTED = any(os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT", "RENDER", "FLY_APP_NAME"))
USER, PASSWORD = metabase.env("APP_USERNAME"), metabase.env("APP_PASSWORD")
REFRESH_HOURS = range(8, 23)          # IST, hourly on the hour

_refresh_lock = threading.Lock()
_page = {"gz": b"", "version": ""}
# Open pages reload themselves when newer data has arrived (not while a records pop-up is open).
AUTO_RELOAD = """<script>
(function () {
  var shown = "%s";
  setInterval(function () {
    if (document.hidden || document.querySelector('#rawModal.open')) return;
    fetch('/version').then(function (r) { return r.ok ? r.text() : shown; })
      .then(function (v) { if (v !== shown) location.reload(); }).catch(function () {});
  }, 60000);
})();
</script>
</body>"""


def log(*a):
    print(dt.datetime.now(pipeline.IST).strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def load_store():
    return json.loads((STORE if STORE.exists() else SNAPSHOT).read_text(encoding="utf-8"))


def save_store(store):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(STORE)


def rebuild_page(store=None):
    store = store or load_store()
    version = store["as_of"]
    page = render(store).replace("</body>", AUTO_RELOAD % html.escape(version))
    _page.update(gz=gzip.compress(page.encode("utf-8"), 6), version=version)


def do_refresh(full=False):
    """Pull, save, re-render. False when a refresh is already running."""
    if not _refresh_lock.acquire(blocking=False):
        return False
    try:
        t = time.time()
        store = pipeline.refresh(load_store(), full=full)
        rebuild_page(store)               # render first: a store that cannot render is not saved
        save_store(store)
        log(f"refreshed in {time.time() - t:.0f}s")
        return True
    finally:
        _refresh_lock.release()


def background_refresh():
    """True when the data is fresh (or another refresh is already running)."""
    try:
        do_refresh()
        return True
    except Exception as e:
        log("refresh failed:", e)
        return False


def scheduler():
    """Refresh once per clock hour. Short sleeps, so it catches up after the PC has been asleep."""
    last = None
    while True:
        now = dt.datetime.now(pipeline.IST)
        hour = (now.date(), now.hour)
        if hour != last:
            due = now.hour in REFRESH_HOURS or not STORE.exists()
            if not due or background_refresh():
                last = hour
            else:
                time.sleep(270)             # failed: try again in about five minutes
        time.sleep(30)


class Handler(BaseHTTPRequestHandler):
    server_version = "InboundDashboard"

    def log_message(self, fmt, *args):      # never log query strings
        pass

    def reply(self, code, body, ctype, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.reply(code, json.dumps(obj, default=str).encode(), "application/json")

    def authorised(self):
        if not (USER and PASSWORD):
            return not HOSTED               # never open on the internet: /raw has phone numbers
        kind, _, token = self.headers.get("Authorization", "").partition(" ")
        try:
            given = base64.b64decode(token) if kind.lower() == "basic" else b""
        except ValueError:
            given = b""
        return hmac.compare_digest(given, f"{USER}:{PASSWORD}".encode())

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/healthz":
            return self.reply(200, b"ok", "text/plain")
        if not self.authorised():
            return self.reply(401, b"Login required", "text/plain",
                              [("WWW-Authenticate", 'Basic realm="Inbound dashboard"')])
        if url.path == "/":
            gz = _page["gz"]
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                return self.reply(200, gz, "text/html; charset=utf-8", [("Content-Encoding", "gzip")])
            return self.reply(200, gzip.decompress(gz), "text/html; charset=utf-8")
        if url.path == "/version":
            return self.reply(200, _page["version"].encode(), "text/plain")
        if url.path == "/refresh":
            if not metabase.configured():
                return self.json({"status": "ERROR: Metabase is not configured on this server"})
            threading.Thread(target=background_refresh, daemon=True).start()
            return self.json({"status": "started"})
        if url.path == "/raw":
            q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            try:
                return self.json(pipeline.raw_records(*(q.get(k, "") for k in ("cls", "code", "scope", "date", "ym"))))
            except Exception as e:
                log("raw failed:", e)
                return self.json({"error": html.escape(str(e))})
        return self.reply(404, b"Not found", "text/plain")


def main():
    if "--refresh" in sys.argv or "--full" in sys.argv:
        do_refresh(full="--full" in sys.argv)
        return
    rebuild_page()
    if metabase.configured():
        threading.Thread(target=scheduler, daemon=True).start()
    else:
        log("Metabase not configured: serving the saved data, no live refresh")
    port = int(os.environ.get("PORT", 8095))
    host = "0.0.0.0" if HOSTED else "127.0.0.1"
    log(f"http://localhost:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
