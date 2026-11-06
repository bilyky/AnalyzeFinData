# 0001 — Modular services in one process

**Status:** Accepted, 2026-11-05. Supersedes the R&D #44 "scenario" package design.

## Context
- **The daily run's logic is a god-function split into copies.** `ai_portfolio_game.py` is 1,531
  lines. In `aether/scenario/steps.py`, its 11 stages reach back into it through 130 `game.X`
  calls (`_pkg()`), so the dependencies point the wrong way. Storage, prices, calendar, cache
  healing, buy execution and reporting all live together.
- **PROD is one Windows machine** that runs scheduled tasks and a FastAPI dashboard (`server.py`).
- **We want clear ownership per concern, and the option to run parts separately later.**

## Decision
- **Six services:** AETHER is organized as `market_data`, `research`, `portfolio`, `trading`,
  `reporting` and `ledger`, under `aether/services/`, plus a shared kernel in `aether/core/`.
- **Same layout for each service:** `api.py` (public facade + DTOs + `API_VERSION`), `domain/`,
  `ports.py`, `app.py`, `adapters/`, and an optional `http.py` FastAPI router.
- **Boundaries:** services talk to each other only through `api.py`. Each service owns its data.
- **One process for now.** The CLI and the dashboard import the services directly.
- **Splitting later:** a service moves into its own process by running its `http.py` router;
  callers then switch to an HTTP client adapter of the same port.
- **Enforced:** an architecture test fails if a module imports another service's internals, if a
  domain module imports I/O, or if anything under `aether/` imports `ai_portfolio_game`.

## Why
- One owner per concern makes each concern readable, fixable and testable in one place.
- Stable public interfaces keep the option to split open, with no cost today.
- Separate processes now would add ports, startup order, health checks and retries on a single
  machine, with no current need.

## Rejected
- **Keep the scenario package and `_pkg()`:** it is the inverted dependency we are removing.
- **Separate processes from the start:** operational cost without a need yet.
- **A generic pipeline engine (Step protocol, RunContext):** it has no second consumer, and plain
  application services read more clearly.

## Consequences
- The validator's inline-import exemptions for `aether/scenario/*` go away once the package is
  deleted.
- Every structural change keeps the behaviour golden byte-identical (ADR 0005).
