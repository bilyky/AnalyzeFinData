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

There are two credentials with very different lifetimes, and the durable one is
**not** the JWT:

- **`sessionToken`** (`jwttoken`) — a 420-char JWT that expires **~7 days** out. It is
  the in-window refresh credential, but it lapses weekly, so it is *not* the thing that
  keeps auth alive long-term.
- **`cf_clearance`** — the Cloudflare clearance cookie in the persistent Chrome profile
  (`Data/chaikin_chrome_profile`), lifetime **~355 days**, re-minted on each successful
  login. This is the real durable credential: a profile that logs in regularly stays
  warm ~indefinitely, and its presence is what lets a *headed* browser pass Turnstile
  with no human.

The captured `sessionKey` + `sessionToken` + `email` also work directly on data calls,
which is what `session.json` holds:

```
session.json = { jsessionid: <sessionKey>, jwttoken: <sessionToken>, uuid: <email> }
```

### Refresh ladder (`powergauge.ensure_valid_session`)

1. **In-window (no browser).** While the `sessionToken` is still valid, mint a fresh
   `sessionKey` via
   `GET /api/authenticate/getJWTAuthorization?acquireSessionForcibly=Yes&jwtToken=<sessionToken>`.
   **Verified live (2026-09-09):** this returns a session **only** when the call carries
   `acquireSessionForcibly=Yes` **and** the current session headers
   (`jwttoken`/`jsessionid`/`x-session-id`/`uuid`) alongside `x-api-key`/`x-app-id`/UA.
   `x-api-key`+`x-app-id` alone return HTTP 200 with an **empty** `sessionId`/
   `omniSessionKey`. The response `sessionId` == `omniSessionKey` (24-char) is the new
   `jsessionid`. This is `powergauge._jwt_to_session_id(session)`. A 200-but-empty
   response means the `sessionToken` itself expired → returns `""` → fall to step 2.
2. **sessionToken expired (weekly, no human).** `_login_via_browser` launches a **headed**
   Chrome via `launch_persistent_context(Data/chaikin_chrome_profile)`. The profile's
   `cf_clearance` makes Turnstile auto-pass, so the login self-completes and mints a fresh
   7-day `sessionToken`. **Headless FAILS** the same flow even with `cf_clearance` (the
   fingerprint trips Turnstile). It needs a desktop session but no interaction. A weekly
   scheduled task (`scripts/monitoring/chaikin_reauth.py`) runs this proactively — calling
   `_login_via_browser(headless=False)` directly — so the token never lapses.

   `login()` picks headed vs headless by its `interactive` flag: an interactive/desktop
   run is **headed** (as above), while the automated ranking-path renewer
   (`login(interactive=False)`) is **headless** so it *fast-fails* rather than hanging ~60s
   on Turnstile before the circuit breaker trips — that reactive path can't solve Turnstile
   anyway, so re-minting is left to the proactive headed task. `CHAIKIN_HEADLESS_LOGIN`
   overrides either way (`1/true/yes` forces headless, `0/false/no` forces headed).
3. **cf_clearance expired (~yearly) or Turnstile blocks.** The circuit breaker trips and
   one throttled email alert is sent; a human logs in once (headed) to re-warm the
   profile. Tokens can also be captured via a warm, human-logged-in Chrome over CDP
   (`scripts/diagnostics/chaikin_cdp_attach_capture.py`); the CAPTCHA is never automated.

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
