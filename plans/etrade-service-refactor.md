# Plan: Encapsulate E*TRADE as an extensible `aether/etrade/` package (microservice/k8s + DB-ready)

> Reference plan saved 2026-08-19. Status: **approved, not yet implemented.**
> Origin: request to "generalize E*TRADE implementation in one separated object … easy to
> extend, add interfaces for new APIs (microservices approach, k8s, run only etrade service
> separately and scalable)", extended with an abstracted/DB-ready data layer and a
> multi-corner architecture review.

## Context

**Why:** The E*TRADE integration is today a single ~921-line `aether/etrade.py` module
(re-exported by a 3-line root `etrade.py` shim) — hard to extend (every new API means more
free functions), and its **state is scattered across raw `Data/*.json` files** that block
both a clean DB migration and stateless scaling. The user wants (1) **one cohesive,
extensible object organized as an `aether/etrade/` package** with obvious extension points;
(2) an **abstracted data layer** (file-backed now, swappable to a DB connector later) with a
**defined local schema**; and (3) a **microservice/k8s-ready** design so E*TRADE can run as
its own service.

**Hard constraint from exploration:** 22 files depend on module-level names; tests patch them
via `mock.patch.object(etrade, ...)`, patch consumer namespaces
(`ai_portfolio_game.etrade.get_tokens`), and **reassign path constants** (`_TOKEN_PATH`,
`_BROWSER_STATE_PATH`, `_REAUTH_STATE_PATH`). The package conversion and data-layer swap
**must keep every seam working**.

**Ban-safety reality:** stateful, ban-sensitive auth — one token per env, midnight-ET expiry,
escalating re-auth breaker (15→30→60→120→240, cap 360), a cross-process **file lock so only
one browser ever opens**, Playwright **interactive OTP** login that can't run headless. So:
single-writer auth/token manager + stateless data-plane.

**Persistence today:** `database.py` is a **legacy, unused** raw-SQL Postgres stub (no ORM,
imported by nothing). Live persistence = JSON files under `Data/`. Config already exposes
`CFG.database_url` / `DATABASE_URL` (env-over-json) — reuse it.

## Architecture review — corners the naive design misses (drives the phases below)

- **Scaling a data-plane on ONE OAuth credential can *raise* ban risk.** E*TRADE rate
  limits + bot-detection are per-credential; N pods polling multiply request volume against
  the single token. ⇒ Phase 4 adds **single-flight request coalescing + a shared rate
  limiter** in front of market data. **Right-size expectations:** the real wins are
  isolation, independent deploy, extensibility, and the data abstraction — NOT unbounded
  horizontal scale (throughput is capped by the per-credential budget).
- **`get_tokens` is unsafe on a data pod** — it renews, can open a browser, and deletes the
  token file. ⇒ add a **read-only `current_token(env)` accessor** (no renew/browser/writes)
  for the data-plane; full `get_tokens` is **auth-role-only**, guarded by a `role` flag on
  the client.
- **Auth pod is a SPOF** (replicas:1 is mandatory for ban-safety). ⇒ alert on breaker
  escalation; data-plane serves cached data with a **staleness flag** when the token is
  stale; documented RTO + re-bootstrap runbook.
- **Token rotation race across pods** at midnight ET. ⇒ token **generation/version** + a
  read-through refresh; document k8s Secret sync lag (~1 min).
- **`verify=False` on every requests call** (TLS verification globally disabled). ⇒ harden:
  trust the proxy CA and drop the blanket `verify=False` (security fix, behavior-preserving).
- **OTP bootstrap can't run in a pod.** ⇒ **bootstrap runbook**: human runs the Playwright
  flow on a workstation → seeds a k8s Secret; the auth pod only *maintains* via `keep_alive`.
- **`ZoneInfo("America/New_York")`** (used by `_et_today`) fails on slim Linux images. ⇒ add
  `tzdata` to the image.
- **HTTP error contract** for the tri-state probe / `None` returns. ⇒ 503 token-unavailable,
  502 broker error, 200 data — defined in Phase 4.
- **No observability** on a ban-sensitive service. ⇒ Prometheus metrics (token age, breaker
  state, API error rate, quote latency), structured logs, trace across the new HTTP hop.
- **DB-down policy** once a DB backend exists. ⇒ fail-fast in multi-node; file fallback only
  single-node; backfill idempotent.
- **CI skips native deps.** ⇒ a new build/test lane for the service image.
- **Large-refactor blast radius.** ⇒ **strangler / parallel-run** with per-phase feature
  flags (`ETRADE_SERVICE_URL`, `DATABASE_URL`, `role`) so each phase is independently
  reversible.
- **Orders idempotency (future).** ⇒ the `orders` stub contract takes a client idempotency
  token to prevent double-fills on retry.

## Security cleanup (do first)
`etrade_config.json` (root) is **orphaned but holds real plaintext credentials**; no code
reads it. **Rotate those credentials, then delete the file**; keep only the `.example`.
Never print any value.

## Package layout — `aether/etrade/`
Root `etrade.py` shim unchanged (a package imports identically). Layout:
```
aether/etrade/
  __init__.py        # facade + BACK-COMPAT SEAM (re-exports every patched name); default singleton
  client.py          # ETradeClient(role=auth|data) — composes .auth/.market/.accounts/.orders/.alerts
  endpoints.py       # _Endpoints(env): renew/revoke/acctlist + the URLs is_market_open_now hardcodes
  config.py          # _load_config (CFG secrets + proxy env wiring)
  auth.py            # get_tokens (auth-role) + current_token (read-only) + keep_alive/renew/revoke/_probe/_login_headless
  reauth_breaker.py  # ReauthCircuitBreaker (was _reauth_* fns; same schema/backoff)
  market.py          # fetch_quotes/get_market/is_market_open_now (+ options_chain — NEW)
  accounts.py        # fetch_positions/get_accounts/_walk
  orders.py          # NEW stub — ETradeOrder place/cancel/list (+ idempotency token)
  alerts.py          # NEW stub — GET /v1/user/alerts
  store.py           # NEW — persistence PORTS (ABCs) + file/JSON adapters
  store_db.py        # NEW — SQLAlchemy DB adapter (stub, future backend)
  remote.py          # NEW — httpx client mirroring the public API
```
Reuse `aether/token_renewer.py`. Do **NOT** touch `aether/circuit_breaker.py` (unrelated
market-crash gate; the *auth* breaker becomes `reauth_breaker.py`).

### ⚠️ Correctness rule the folder split forces (patch-seam preservation)
Submodule orchestrators call patched seams **through the package object** —
`from aether import etrade as _pkg; _pkg._probe_token_auth(...)` (lazy import) — NOT bare
same-module references, so `mock.patch.object(etrade, ...)` still intercepts. `__init__.py`
re-exports every seam. **The existing E*TRADE test suite is the acceptance gate.** Re-exports
must include: public (`get_tokens`, `fetch_quotes`, `fetch_positions`, `get_accounts`,
`get_market`, `is_market_open_now`, `keep_alive`, `renew_tokens`, `revoke_tokens`,
`reset_reauth_circuit_breaker`); patched privates (`_load_config`, `_probe_token_auth`,
`_login_headless`, `_load_tokens`, `_load_tokens_any_date`, `_load_reauth_state`,
`_reauth_cooldown_remaining`, `_record_reauth_result`, `_save_tokens`,
`_get_tokens_via_playwright`, `_et_today`); constants `_TOKEN_PATH`/`_BROWSER_STATE_PATH`/
`_REAUTH_STATE_PATH` (package-level, read at call time so the file store honors reassignment).

## Phases

### Phase 1 — Package + `ETradeClient` (behavior-identical, in-process)
Split the module into the files above; `ETradeClient(env, *, role="auth", config=CFG,
store=..., allow_browser=False)` with the five sub-interfaces. `role="data"` disables
renew/browser/writes and exposes only `current_token` + read paths. Centralize endpoint maps
(also fixes the is-market-open prod hardcode **without changing prod behavior**). Encapsulate
the breaker as `ReauthCircuitBreaker` (same on-disk schema/paths/backoff).

### Phase 2 — Abstracted persistence (Ports & Adapters) + local schema
`make_etrade_store(config)` selects backend by config (`DATABASE_URL` empty ⇒ file backend =
today's behavior; set ⇒ DB). Ports (ABCs in `store.py`), each with a defined record schema:
- **`TokenStore`** — `load(env)`(same-day guard)/`load_any_date`/`save`/`delete`; record
  `env, oauth_token, oauth_token_secret, saved_at, issued_date_et, generation`. **Secret.**
- **`BrowserStateStore`** — `load/save(blob)`; Playwright storage_state. **Secret.**
- **`ReauthStateStore`** — `load/save/reset(env)`; `env, consecutive_failures, last_attempt,
  cooldown_until`.
- **`LockProvider`** — `acquire(name, ttl)/release`; file-lock adapter now (wraps
  `TokenRenewer` O_EXCL), **DB row-lease adapter later** (preserves "one browser opens"
  across pods).
- **`AuthEventLog`** (the local **trace**) — append-only, **secret-free**: `ts, env, event
  (issued|renewed|revoked|probe_ok|probe_fail|reauth_*|cooldown_set), detail, source`.
  File adapter = JSONL under `Data/`.
- *(optional)* **`PositionSnapshotStore`** — positions history trace.

File adapters **read the module-level path constants at call time** (tests reassign them).
**Secrets nuance:** the DB adapter must NOT store raw OAuth secrets/browser-state in a plain
table — those stay in a k8s Secret (or envelope-encrypted); the **DB holds only breaker
state + audit + snapshots** (non-secret, shareable, data-plane-readable).

### Phase 3 — Extension points
- `market.options_chain(symbol, expiry=None)` — **the interface the INTC collar adviser
  needs** (none exists today); wraps `pyetrade.ETradeMarket.get_option_chains`; supports
  expiry/strike filtering (payload can be large).
- `orders.place/cancel/list` — `ETradeOrder` wrappers with a client **idempotency token**;
  raise `NotImplementedError` + TODO initially.
- `alerts.list(since=None)` — `/v1/user/alerts` stub.

### Phase 4 — Standalone FastAPI service (`services/etrade_service/app.py`)
Mirror `server.py`'s `create_app()` + inline routes + `run_in_executor` + fail-closed Bearer.
Routes: `GET /health`, `GET /ready` (token present/unexpired), `GET /v1/quotes?symbols=`,
`GET /v1/positions`, `GET /v1/market/clock`, `POST /v1/tokens/keep-alive`. Add:
- **Single-flight + shared rate limiter** in front of `fetch_quotes` (coalesce concurrent
  same-symbol requests; cap request rate on the shared credential) — the ban-risk mitigation.
- **Error contract:** 503 token-unavailable, 502 broker error, 200 data (+ `stale: true`
  when serving cached data past token freshness).
- **Metrics** (`/metrics` Prometheus: token age, breaker state, API error rate, latency).
- `aether/etrade/remote.py` (httpx) mirrors the public API; `__init__` delegators route to it
  when `ETRADE_SERVICE_URL` is set — consumers reach the service **without call-site changes**.

### Phase 5 — Container + k8s + DB migration
- **DB migration = schema + data.** Adopt **Alembic** under `deploy/migrations/` (repo has
  none; SQLAlchemy already a dep). Initial revision creates the **non-secret** tables:
  `etrade_reauth_state`, `etrade_lock` (row-lease), `etrade_auth_event` (indexed `(env, ts)`),
  `etrade_position_snapshot`. One-time **file→DB backfill** command (idempotent) so flipping
  `DATABASE_URL` is a clean cutover.
- **Container/k8s.** `Dockerfile` on `mcr.microsoft.com/playwright/python` (Chromium+libs) +
  **`tzdata`** + curated `requirements-etrade.txt` (pyetrade, requests-oauthlib, playwright,
  pytz, fastapi, uvicorn[standard], httpx, sqlalchemy, alembic, prometheus-client).
  `deploy/k8s/`: **`etrade-auth` Deployment replicas:1** (owns token Secret, runs `keep_alive`,
  breaker escalation alert; OTP bootstrap = documented human step) + **`etrade-data`
  HPA-scaled** stateless plane (`role=data`, read-only token, breaker/audit from DB) +
  `Service` + `HPA` + `Secret` (7 `ETRADE_*`) + optional `DATABASE_URL` Secret. LockProvider →
  DB row-lease in multi-pod. Plus a **CI build/test lane** for the image; **bootstrap runbook**
  doc; strangler cutover notes.

## Execution recommendation (scope of this pass)
Execute **Phase 1 + 2 + security cleanup** as real, tested code (extensible package + data
layer with today's file backend default — no behavior change). Deliver **Phase 3 + 4 + 5** as
new, side-effect-free files (extension stubs, service + single-flight/metrics, DB
adapter/migrations, Docker/k8s, runbook) that add capability **without altering the running
monolith** (opt-in via `ETRADE_SERVICE_URL` / `DATABASE_URL` / `role`). Phases are
independent and individually reversible.

## Critical files
- Refactor in place → package: `aether/etrade/*` (root `etrade.py` shim untouched).
- Reuse: `aether/token_renewer.py`; `CFG.database_url`/`DATABASE_URL`; `server.py`
  `create_app()` template.
- Repurpose legacy `database.py` engine/URL plumbing into `store_db.py` (or retire it).
- Do NOT touch: `aether/circuit_breaker.py`.
- New: `aether/etrade/{store,store_db,remote}.py`; `services/etrade_service/*`;
  `deploy/k8s/*.yaml`; `deploy/migrations/*`; bootstrap runbook.
- Delete orphaned `etrade_config.json` after rotation.

## Verification
1. **Seam preservation (highest priority):** `python -m unittest tests.test_etrade_token_auth
   tests.test_etrade_auth_scenarios tests.test_etrade_reauth_circuit_breaker
   tests.test_etrade_live tests.test_live_api_contract tests.test_custom_sprints` green across
   the new package + store boundary; then full `discover tests` stays green.
2. **Data-layer parity:** file `TokenStore`/`ReauthStateStore`/`AuthEventLog` round-trip
   against today's `Data/*.json` shapes; `_TOKEN_PATH` reassignment redirects the store;
   `make_etrade_store` selects file vs DB by `DATABASE_URL`.
3. **Role guard:** `ETradeClient(role="data").auth.get_tokens()` raises / is unavailable;
   `current_token` never renews or writes (assert no file mutation, no browser call).
4. **Behavior identity:** diff-test module fns vs `ETradeClient` methods under mocked
   pyetrade/OAuth; `is_market_open_now` still hits production clock/SPY for prod.
5. **Service + single-flight + DB (if run this pass):** concurrent same-symbol requests
   collapse to one broker call; error contract returns 503/502/200 correctly; `/metrics`
   exposes token age + breaker state; `alembic upgrade head` + file→DB backfill reproduces
   breaker/audit state; `kubectl apply --dry-run=client -f deploy/k8s/` validates.
6. **No secrets in any output** (tokens/accounts/credentials/browser-state never printed).
