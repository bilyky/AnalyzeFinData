# Chaikin `/api/*` data layer — contract & adapter

**Status:** SHIPPED (2026-08-31). This document is the doc-sync surface for the
`chaikin_api` anchor in `powergauge.py` (see
`scripts/utils/pre_commit_validator.py :: DOC_SYNC_SURFACES`). If you change the
new→legacy adapter, the rating maps, the header contract, or the endpoint, update
this file in the same commit.

## Why this exists

Chaikin migrated its data backend. The legacy Java API
(`members-backend.chaikinanalytics.com/CPTRestSecure/app/*`) now returns nginx
**503 "no healthy upstream"** for everyone — the old upstream is gone. The new
backend is a Fastify API at the same host under **`/api/*`**. `powergauge.py` used
to call the dead `/CPTRestSecure/app/portfolio/getSymbolData` (plus a second
`industry_url` call), so every fetch and the session probe failed.

## Endpoint

`GET /api/suggestions/{symbol}` is the single drop-in replacement for the legacy
`getSymbolData` pair. One call returns a flat bundle with everything the screener
extracts (PGR rating, checklist, price, signals, sector/industry). This halves the
per-symbol fetch count (one GET instead of the old symbol + industry pair).

OHLCV bars are **not** sourced here — they come from `Symbol_full/*_daily.json`
(RapidAPI). The Chaikin chart endpoint (`GET /api/v2/chart/{symbol}`) exists but is
not wired; `get_symbol_data` does not fetch OHLCV.

## Header contract

Every data call sends these header **names** (values are not reproduced here):

| Header | Value source |
| --- | --- |
| `jwttoken` | `session.json["jwttoken"]` (durable session token, JWT) |
| `jsessionid` **and** `x-session-id` | `session.json["jsessionid"]` (same value in both) |
| `uuid` | `session.json["uuid"]` (account email) |
| `x-api-key` | `_CHAIKIN_API_KEY` — the OMNI client key. **No default is shipped in source** (public repo; the value's secret-vs-public status is unverified). Supply it via `CFG.chaikin_api_key` (config.json) or the `CHAIKIN_API_KEY` env var; the header block comment in `powergauge.py` documents how to read the live value from a logged-in OMNI session |
| `x-app-id` | `omni` |

No cookie is required (a replay with no cookie returns 200). `beaconStreetJwtToken`
is unused by the data API.

**Common failure:** an empty `x-api-key` returns `403 {"code":"SESSION_EXPIRED",
"message":"Missing required headers"}` — misleading; it is the missing key, not an
expired token. Because no key is defaulted in source, an unconfigured deployment
(no `CFG.chaikin_api_key`, no `CHAIKIN_API_KEY`) hits exactly this 403 and the
session probe reports `unreachable`/`invalid` until the key is set.

## Credential model

`sessionToken` is the durable refresh credential (live tokens expire ~7 days out).
The app mints a per-run session via
`GET /api/authenticate/getJWTAuthorization?jwtToken=<sessionToken>`. The captured
`sessionKey` + `sessionToken` + `email` also work directly on data calls, which is
what `session.json` holds:

```
session.json = { jsessionid: <sessionKey>, jwttoken: <sessionToken>, uuid: <email> }
```

A fully headless refresh (mint a fresh `sessionKey` from `sessionToken` with no
browser) is **not yet confirmed** — a bare replay of `getJWTAuthorization` returned
403; it likely also needs the session headers. Not blocking while the captured
token has runway. Tokens are captured via a warm, human-logged-in Chrome over CDP
(`scripts/diagnostics/chaikin_cdp_attach_capture.py`); the CAPTCHA is never
automated.

## Adapter: new bundle → legacy schema

`powergauge._adapt_suggestions_to_legacy(data, symbol)` reshapes the new flat bundle
into the legacy `{status, pgr[7], metaInfo[1], checklist_stocks{}}` schema, so
`init_from_json`, `_check_schema`, the on-disk cache format, and `find_prev_pf`
stay unchanged.

### Rating scale (7-level → legacy 5-level)

The new `pgrRating` is a **7-level** scale (1 = Very Bearish … 7 = Very Bullish, with
Neutral −/·/+ granularity). Legacy code expects the old **5-level** rating. Both an
integer map (`_RATING_INT7_TO_OLD5`) and a name map (`_RATING_NAME_TO_OLD5`) collapse
the Neutral −/·/+ band to old `3`; `0` = unrated (e.g. leveraged/inverse ETFs with no
PGR). `_pgr_rating_old5(int_rating, name)` resolves int first, then falls back to the
name.

### Invalid symbol

An unknown ticker still returns HTTP 200 but with an empty `checklistData` and null
`name`. The adapter surfaces this as `{"status": "invalid symbol"}`, and the caller
sets `price = -1` — matching the old API's behavior. A non-`"ok"` bundle is **never
written to the on-disk cache** (cache-write guard in `get_symbol_data`), so a
transient degraded 200 cannot poison a symbol's cache and re-serve it as invalid on
later cache-preferred reads.

## Tests

- `tests/test_pgr_adapter.py` — pins the new→old mapping (rating buckets, checklist
  vocab, signals string, invalid symbol, round-trip through `init_from_json`).
- `tests/test_doc_sync.py` — the doc-sync guard and registry-consistency checks that
  keep this surface tied to the `chaikin_api` anchor.
