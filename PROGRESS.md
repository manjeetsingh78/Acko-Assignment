# Pulse – Progress log

> Written during the build with my AI assistant; I reviewed and edited it into my own words.
> The Gate-2 version of this file (what worked at 3:30) is preserved in git history.

## Where the project stands (final)

- **All 10 must-haves are built, tested and running** with one command: `docker compose up --build`.
  The product owner asked for the **backend only** (D47), so the operator dashboard and the public
  status page are delivered as JSON APIs; `/` redirects to the interactive Swagger UI, which acts
  as the operator console.
- **121 unit + integration tests and 7 acceptance tests pass.** The acceptance suite drives the
  brief's 10 scenarios against the live Compose stack (real kit services, real webhook receiver).
- The fast incident drill behaves exactly as predicted: claims down → 1 incident + 2 webhooks;
  payments slow → DEGRADED with **no** incident; quotes flaky → intermittent DEGRADED, no incident.
- Extras beyond the must-haves: operator login, 7-day availability window, per-service flaky
  incident switch (off by default, PO R6), a single-scheduler guarantee via a Postgres advisory
  lock, and a transcript export script.

## How I got here (timeline)

### 0:15 – 1:15 · Discovery (Gate 1)
- Chose **Option 1: Pulse**. Wrote the problem in my own words for four users: on-call engineers,
  support, customers and leads (PLAN.md §1).
- Researched Statuspage, Uptime Kuma, Better Stack, PagerDuty and UptimeRobot; adopted their
  common patterns: anti-flapping thresholds, incident dedup, Open → Acknowledged → Resolved,
  fixed customer-facing wording (PLAN.md §3).
- Prepared 13 questions for the product owner and made working decisions for each (PLAN.md §2).

### 1:15 – 3:30 · Build to Gate 2
- Skeleton: FastAPI + SQLAlchemy models + catalogue CRUD + seeding of the 4 kit services.
- The PO's answers reshaped the plan mid-build; each change is logged with a reason in
  DECISIONS.md:
  - SQLite → **PostgreSQL 16** (D19)
  - per-service intervals by **criticality tier** 10/15/20/30 s (D20)
  - recovery needs **3** consecutive successes, with a visible **RECOVERING** state (D23, D25)
  - incidents are **edge-triggered** on entering DOWN, several per service allowed, dedup by
    `trigger_key` (D27)
  - **operator login** with a signed session cookie (D31)
- State machine, probe, check processor, scheduler and the webhook **outbox** with retries (D29).
- Gate 2 commit: *"Backend has been created and some services has been created"*.

### 3:30 – 5:30 · Build, test, metrics
- Public (customer-safe) API separate from the operator API, enforced by separate Pydantic
  schemas and fixed template text only (D30, D13).
- Reliability: 24 h availability with a covered-time caveat and the error budget in minutes (D32).
- Product metrics: MTTD, MTTA, MTTR, scheduler performance, webhook delivery stats.
- Flaky detection from stored checks (D26, refined by D41) + optional flaky incidents (D49).
- **Frontend detour, honestly:** I built a React UI (D44–D46), then the PO answered
  "backend only" — the React source was archived outside the repo and the product ships as an
  API-first backend (D47). Time cost, but the API design it forced is what I demo.
- Acceptance suite written to run the brief's 10 scenarios against the live stack (D43: the tests
  PATCH services to 2 s intervals for speed, then restore the config).

### 5:30 – 6:00 · Finish and verify
- Full verification session on the final stack:
  1. `pytest` — 121 passed (after fixing the two infra bugs below).
  2. `docker compose up --build`, then `pytest -m acceptance` — 7/7 passed (~2.5 min).
  3. Manual fast drill, observed live (claims, high tier = 15 s interval):
     `down` at 20:21:11 → DEGRADED at 20:21:29 (first failed check) → **DOWN + incident +
     `incident.opened` webhook at 20:21:42** (2 consecutive failures, MTTD ≈ one interval) →
     `reset` at 20:21:52 → RECOVERING 1/3, 2/3 → **HEALTHY + resolved +
     `incident.resolved` webhook by 20:22:34** (exactly 3 successes). Payments `slow 3000`
     showed DEGRADED with no incident; quotes flaky showed intermittent DEGRADED; every
     service back to Operational after the drill.
- Final documentation set completed: README (run + 3-minute demo + troubleshooting), this file,
  DESIGN.md, ARCHITECTURE.md, TESTING.md (every requirement + scenario recipes + verification log),
  REVIEW.md, and the two fixes logged as D54/D55 in DECISIONS.md.

## Bugs I found and fixed

1. **Advisory-lock connection was pooled** — closing it returned a session to the pool with the
   lock still held, so a "standby" instance could have started a second scheduler. Fixed by
   detaching the connection from the pool before close (D50). Caught by a test.
2. **Outbox rows scheduled with Postgres `now()`** while the sender compared against the app
   clock — retries never fired. Fixed by stamping `next_attempt_at` from the app clock (D42).
3. **`init_db` ran `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on every start** — the statement
   takes an AccessExclusive lock even when it skips, which deadlocked check processing under
   concurrency during the final verification session. Fixed by probing `information_schema` and
   altering only when the column is genuinely missing.
4. **All tests shared one `pulse_test` database** — two overlapping pytest runs raced
   `drop_all`/`create_all` (pg_type clashes and deadlocks). Fixed by giving every pytest session
   its own throwaway database (`PULSE_TEST_DATABASE_URL` override still honoured).

## Changes from the Gate 1 plan (summary; full reasons in DECISIONS.md)

| Gate 1 plan | Final | Why |
|---|---|---|
| SQLite on a Docker volume | PostgreSQL 16 in Compose | PO requirement (D19) |
| One interval for all | Tiers 10/15/20/30 s by criticality | PO (D20) |
| 2 successes recover | 3 successes + RECOVERING state | PO (D23) |
| 1 open incident per service | Several allowed, edge-triggered + dedup key | PO (D27) |
| Auth deferred | Simple operator login | PO "if possible" (D31) |
| Jinja server-rendered UI | Backend-only, Swagger as console | PO (D47) |
| Degraded opens nothing, ever | Unchanged for DOWN; optional flaky incidents | PO R6 (D49) |

## What works → how it is verified

| # | Must-have | Verified by |
|---|---|---|
| 1 | Service catalogue (API + validation) | unit + integration + acceptance |
| 2 | Independent scheduled checks, every result stored | scheduler-isolation tests; live drill |
| 3 | HEALTHY / DEGRADED / DOWN / RECOVERING + flaky | table-driven unit tests |
| 4 | Auto incidents: open/resolve, ack, notes, timeline | integration + acceptance |
| 5 | Availability vs target + error budget | hand-checked availability tests |
| 6 | Operator dashboard | `/api/dashboard/summary` tests |
| 7 | Public status page data, no internal leaks | leak-marker tests on both public APIs |
| 8 | Webhooks on open + resolve, retries, history | notifier tests; live receiver count = 2 × incidents |
| 9 | Product metrics (MTTD/MTTA/MTTR, scheduler, webhooks) | `/api/metrics/summary` tests |
| 10 | One-command run + README | fresh `docker compose up --build` |

## What I would do next (not done)

- Manual incident creation and manual resolve (PO hasn't answered Q4).
- Load-balanced multi-instance scheduling (standby instances serve the API only today).
- Per-service webhook secrets/signatures and richer notification formats.
- Customer subscriptions to the status page (out of scope, D18).
