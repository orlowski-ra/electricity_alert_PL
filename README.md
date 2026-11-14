# Electricity Alert PL

Command-line tool and Python library that warns about **planned power outages** in Poland for
saved addresses. It reads the public outage pages and APIs of the five Polish distribution system
operators:

| id       | Operator            | Area                  | Source                                   |
|----------|---------------------|-----------------------|------------------------------------------|
| `pge`    | PGE Dystrybucja     | central/east Poland   | JSON API + TERYT town/street dictionary  |
| `tauron` | TAURON Dystrybucja  | south Poland          | JSON API (region-wide lists)             |
| `enea`   | Enea Operator       | west Poland           | HTML search form (no API)                |
| `energa` | Energa-Operator     | north Poland          | one JSON document for the whole network  |
| `stoen`  | Stoen Operator      | Warsaw                | JSON API (all outages, filtered locally) |

> The operators' APIs are unofficial and undocumented. The code treats every response as
> untrusted: every field is optional, unknown fields are ignored, and errors are returned as
> `ApiResult` values (`Success`, `NetworkError`, `HttpError`, `FormatError`, `NotSupported`).
> No exception from the network or from parsing ever reaches the caller.

## Quick start

Requires Python 3.12+.

```
python -m venv .venv
.venv\Scripts\activate          # Windows (on Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
pytest
```

Example (Warsaw, Stoen):

```
electricity-alert providers
electricity-alert outages stoen --param "street=ul. Marszałkowska" --days 4
electricity-alert add stoen --label "Warszawa, ul. Marszałkowska" --param "street=ul. Marszałkowska"
electricity-alert check
```

Example (PGE): find the town and street codes first, then save the address.

```
electricity-alert search pge "Suwałki"
electricity-alert streets pge 0977456 "Suwałki"
electricity-alert add pge --label "Suwałki, ul. Kolejowa" --param citySym=0977456 --param streetSym=08828
```

Data (SQLite database and caches) is stored in `~/.electricity_alert`, or in the folder given by
`--home` or `$ELECTRICITY_ALERT_HOME`.

### Address parameters per operator

| Operator | Required                        | Optional                                             |
|----------|---------------------------------|------------------------------------------------------|
| `pge`    | `citySym`                       | `streetSym`                                          |
| `tauron` | `cityGAID`, `streetGAID`        | `cityName`, `streetName` (narrow results), `houseNo` |
| `enea`   | `city`                          | `street`                                             |
| `energa` | `cityName`                      | `countyCode`, `communeName`, `communeType`, `street` (TERYT codes from `search pge --nationwide`) |
| `stoen`  | `street`                        |                                                      |

## How notifications work

`check` runs one check of all saved addresses (meant to be scheduled, e.g. every 3 hours):

- The window is from now until the end of "today + days" in Polish time (`--days` 1–4).
- A new outage is notified at once, never twice for the same id.
- A reminder about an already notified outage comes at most once a day (21 h apart) and only
  before the outage day.
- After a notification the address is paused until the end of the day (no more reminders today;
  new outages still come through).
- An outage revoked after we notified about it is notified once (`--no-revoked` turns it off).
- Between 22:00 and 07:00 (Warsaw time) notifications are marked as silent.

## Project layout

```
src/electricity_alert/
  models.py        Address, Outage, ApiResult, TimeWindow
  text.py          Polish text helpers, street key words, outage descriptions
  decider.py       pure notification logic (what to notify and when)
  quiet_hours.py   22:00–07:00 silent window
  http.py          HTTP client: errors as values, retries, PGE load-balancer workaround
  jsonutil.py      defensive JSON field readers
  providers/       one module per operator + registry
  storage.py       SQLite: addresses and already-notified outages
  checker.py       one check of all addresses
  cli.py           command-line interface
tests/             pytest; real (trimmed) API responses in tests/fixtures/
```

## Conventions

- Python 3.12+, type hints everywhere, `ruff check` and `ruff format` clean.
- Only one runtime dependency: `httpx`. Tests use `httpx.MockTransport` (no real network).
- Never raise from provider code: return an `ApiResult` error instead. Never crash on bad data.
- Dates of outages are naive `datetime` in Polish local time; moments ("now") are timezone-aware.
- Each behaviour change comes with a test. Run `pytest` and `ruff check src tests` before committing.
- Code and comments in English; user-facing data (street names, descriptions) stays in Polish.
