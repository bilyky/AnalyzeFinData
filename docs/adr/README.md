# Architecture Decision Records

Short records of decisions that shape AETHER's structure: what was decided, why, and what was
rejected. Each record is immutable once accepted. A later decision supersedes an earlier one in
a new record and links back to it.

| # | Decision | Status |
|---|---|---|
| [0001](0001-modular-services-one-process.md) | Modular services in one process (a modular monolith that can be split later) | Accepted 2026-11-05 |
| [0002](0002-domain-model-ports-adapters.md) | A DDD domain model behind ports and adapters, wired in one composition root | Accepted 2026-11-05 |
| [0003](0003-portfolio-store-json-then-postgres.md) | Portfolio storage: JSON by default, PostgreSQL by explicit opt-in | Accepted 2026-11-05 |
| [0004](0004-many-portfolios-and-consumers.md) | Many portfolios (`portfolio_id`) and many consumer projects (versioned service APIs) | Accepted 2026-11-05 |
| [0005](0005-golden-split.md) | Split the daily-run golden test into behaviour and audit logs | Accepted 2026-11-05 |

The plan that applies these decisions is [`plans/aether-services.md`](../../plans/aether-services.md) (R&D #78).
