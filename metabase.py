"""Settings and a small Metabase client (standard library only)."""
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from metabase_key import get_metabase_key

ROOT = Path(__file__).parent


def _load_dotenv():
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv()


def env(name, default=""):
    return os.environ.get(name, default)


def configured():
    return bool(env("METABASE_URL") and env("METABASE_DATABASE_ID") and get_metabase_key())


def fetch(req, timeout, waits=(5, 20, 60)):
    """Send a read-only request; retry when the connection drops or Metabase reports an error.

    A slow answer is not retried: the query may still be running, and sending it again adds load."""
    for wait in (*waits, None):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            problem = f"Metabase returned HTTP {e.code}"
            if e.code < 500 and e.code != 429:
                raise RuntimeError(problem) from None      # wrong key, bad query: retrying will not help
        except TimeoutError:
            raise RuntimeError(f"Metabase did not answer within {timeout} s") from None
        except (OSError, http.client.HTTPException, ValueError) as e:
            if isinstance(getattr(e, "reason", None), TimeoutError):
                raise RuntimeError(f"Metabase did not answer within {timeout} s") from None
            problem = f"Could not reach Metabase: {getattr(e, 'reason', e)}"
        if wait is None:
            raise RuntimeError(problem)
        time.sleep(wait)


def query(sql, params=None, timeout=240):
    """Run a native query and return a list of dicts with UPPER-CASE keys.

    Uses the JSON export endpoint because /api/dataset stops at 2000 rows.
    `params` fill {{tags}} in the SQL; Metabase quotes them, so values from the
    browser are never pasted into the SQL text."""
    if not configured():
        raise RuntimeError("Metabase is not configured. Set METABASE_URL, METABASE_API_KEY and "
                           "METABASE_DATABASE_ID (see .env.example).")
    sql += f"\n-- run {time.time_ns()}"          # unique text, so Metabase cannot answer from its cache
    tags, values = {}, []
    for name, value in (params or {}).items():
        tags[name] = {"id": name, "name": name, "display-name": name, "type": "text"}
        values.append({"type": "category", "target": ["variable", ["template-tag", name]], "value": str(value)})
    q = {"database": int(env("METABASE_DATABASE_ID")), "type": "native",
         "native": {"query": sql, "template-tags": tags}, "parameters": values}
    body = urllib.parse.urlencode({"query": json.dumps(q), "format_rows": "false"}).encode()
    req = urllib.request.Request(env("METABASE_URL").rstrip("/") + "/api/dataset/json", data=body, headers={
        "x-api-key": get_metabase_key(), "Content-Type": "application/x-www-form-urlencoded"})
    data = fetch(req, timeout)
    if isinstance(data, dict):                                   # errors come back as an object
        raise RuntimeError(f"Metabase query failed: {data.get('error') or data.get('message') or 'unknown error'}")
    return [{k.upper(): v for k, v in row.items()} for row in data]
