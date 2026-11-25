# Inbound Call Dashboard (replica)

Replica of the Daily Inbound Call Dashboard (Service Queue): same page, same data logic.
Python standard library only; nothing to install.

    Browser -> server.py -> pipeline.py -> Metabase -> Snowflake (AMEYO_CALL_DETAILS_REPORT)
                   |            |
               render.py    data/store.json (aggregates only)
                   |
               logic.py

| File | What it does |
|---|---|
| `logic.py` | Every calculation: averages, spikes, 7-day averages, MoM, highlights |
| `callback.py` | Callback Calls tab: auto-dialled campaign `wiom_talktime_threshold_outbound`, from July 2026 |
| `render.py`, `templates/page.html` | The page |
| `pipeline.py` | SQL and the refresh; also the drill-through query |
| `metabase.py` | Settings (`.env`) and the Metabase client |
| `server.py` | Web server, hourly refresh 08:00-22:00 IST |
| `verify_live.py` | Compares live SQL with the original's published numbers |
| `data/snapshot.json` | Aggregates taken from the original on 25 Nov 2026, 1 Mar - 25 Nov |
| `tests/` | `python tests/test_dashboard.py` |

## GitHub Pages (shared version)

`.github/workflows/refresh.yml` pulls from Metabase twice an hour, 08:07-22:37 IST (the last 3 days; saved data for the rest), builds the page with
`build_static.py` and publishes it. Nothing is committed on a refresh. Open pages reload themselves.

- Secret (Settings -> Secrets and variables -> Actions): `METABASE_API_KEY`
- Settings -> Pages -> Source: **GitHub Actions**
- Manual refresh: Actions -> Refresh inbound dashboard -> Run workflow
- Aggregates only: no drill-through, and the build stops if anything looks like a phone number.

## Run locally (with drill-through)

    python server.py

Open http://localhost:8095. It refreshes hourly from 08:00 to 22:00 IST. Copy `.env.example` to `.env`; the key comes
from `METABASE_API_KEY` or the encrypted `~/.metabase_key`. `python verify_live.py` compares live SQL with the original.

## Data logic

| Item | Rule |
|---|---|
| Service calls | `QUEUE_NAME IN (high_pain_queue, low_pain_queue)`, `CALL_TYPE = inbound.call.dial` |
| Missed | Disposition class empty |
| Category table | Service calls **with** a disposition (Service calls tagged "Wiom Net" left out) + **all** Wiom Net calls. So its "Missed" row is Wiom Net only, and TOTAL = Service volume - Service missed + Wiom Net volume |
| Sales Queue | Six old `Sales-*` classes rolled into one row |
| MTD average | Mean of complete days (today left out); MoM tab includes today |
| Spike | >= 100: +15 %, 20-99: +25 %, < 20: +100 %. MTD vs previous month's average; a day vs this month's MTD |
| July | Compared with 16-30 Jun instead of all June |
| Missed %, Transferred %, Repeat % (month) | Weighted by calls |
| AHT, Agents, CSAT, Low Pain (month) | Plain mean of daily values |
| Highlights | Top 8 rises. Current month: last 2 complete days vs previous month. Class rows average only days with calls |
| 7-day average (Current) | Mean of up to 7 earlier dates **among the 9 loaded**, so the oldest dates use fewer days |
| Current spikes | Service: +15 % warm, +25 % hot. Wiom Net: the volume-based threshold, hot at 1.5x |
| ISD banner | Sum of each hour's top-5 partners, not all Internet Supply Down calls |
| AHT | Mean talk + wrap-up of calls with talk time, whole seconds |
| Repeat caller % | (calls - unique numbers) / calls, per day |
| Wiom Net metrics | 09:00-21:59 only; repeat % uses all hours |
| CSAT | Ratings 4-5 / ratings 1-5 (`AMEYO_MAIL.FEEDBACKREPORT`) |
| MoM header | Missed % = mean of months excl. Feb; AHT = mean incl. Feb |

## Match with the original (25 Nov 2026, 1 Mar - 24 Nov)

- Category cells: 11,024 of 11,024. Service volume, missed, transfers, Low Pain, CSAT, repeat %: every day.
- Small differences: agents (39 older days) and AHT (about 50 days), mostly by 1; Wiom Net volume on 2-4 Sep;
  plans sold on 5 days, by 1-4.
- Paying / Active R15: about 0.07 % higher than published. Top CSP: within about 3 %. Top ISP: approximate.
- February has monthly averages only; CSAT for Feb-Mar keeps the published scores.

## Deploy (Railway)

Set the `.env` values as variables plus `APP_USERNAME` / `APP_PASSWORD` (required: drill-through shows
customer phone numbers), and `DATA_DIR` on a mounted volume.
