# Pulse – Backend Plan v2 (after the product owner's answers)

> **Why this is not in PLAN.md:** the submission rules say PLAN.md must be "your Gate 1 plan, unchanged (record
> later changes in the decision log)". PLAN.md (commit `393797b`) stays frozen. This file is the current plan.
> Every change is logged in DECISIONS.md. The system design is in ARCHITECTURE.md, and how to test each
> requirement is in TESTING.md.

---

## 1. Problem and scope in one paragraph

Acko's services fail or slow down, and nobody finds out in time. **Pulse is a backend service** that checks every
registered service on its own schedule, decides HEALTHY / DEGRADED / DOWN / RECOVERING, opens and resolves incidents
automatically, notifies the webhook receiver, and exposes everything through a REST API:
- an operator API (login required) for engineers;
- a public, customer-safe API for the status page.

**Product owner decision (D47): build the backend only.** The dashboard and the status page are delivered as JSON
endpoints, and the interactive Swagger UI at `/docs` is the console.

| User | Outcome they need | Served by |
|---|---|---|
| Engineers / SREs | Fast detection, one incident per outage, exact technical detail, ack and notes, a timeline | `/api/*` (operator) |
| Support | What is broken and since when | `/api/dashboard/summary`, `/api/incidents` |
| Customers | Plain-language status, no internal details | `/api/public/status`, `/api/public/history` |
| Leads | Availability against target, error budget | `/api/services/{id}/metrics`, `/api/metrics/summary` |

---

## 2. Product owner answers → what we build

| # | PO answer | Our implementation (default) | Decision |
|---|---|---|---|
| 1 | Tier-based check frequency, everything configurable, no hard-coded names | criticality → interval: critical 10 s, high 15 s, medium 20 s, low 30 s; every knob per service | D20 |
| 2 | Research and recommend the timeout and latency thresholds | timeout **5 s** (PO proposed 3 s), slow > 1 s, 2xx = success; 5 outcome classes | D21, D22 |
| 3 | 2 consecutive failures → DOWN + incident; 1 → DEGRADED; a success resets | `failure_threshold = 2` | D6 |
| 4 | 3 consecutive successes to recover; RECOVERING in between | `recovery_threshold = 3`; a failure while recovering → DOWN, same incident | D23, D24 |
| 5 | Flaky: X failures in Y s → DEGRADED, configurable, no incident for now | 3 failures in max(60 s, 6 × interval); failures while DOWN are not counted | D26, D41 |
| 6 | Multiple unresolved incidents allowed, no duplicates per trigger | edge-triggered on entering DOWN; `UNIQUE(trigger_key)`; recovery resolves only `service_down` incidents | D27, D28 |
| 7 | Webhooks on open and resolve: delivery status, bounded retry, no duplicates | outbox table + sender task; 4 attempts (5/15/45 s); `UNIQUE(incident_id, event_type)` | D29, D42 |
| 8 | Customer-friendly public view vs exact technical operator view | separate Pydantic schemas; public text from fixed templates only | D30 |
| 9 | Rolling 24 h availability, target, error budget, no invented data | count-based availability; "insufficient data" under 10 checks | D32 |
| 10 | Must-haves first | §4 | D17 |
| 11 | PostgreSQL; survive restarts | all state in Postgres; scheduler and outbox resume | D19 |
| 12 | Simple auth if possible | one operator account (env), signed HttpOnly session cookie | D31 |

**Why the defaults differ from the PO's proposals** (to be confirmed with the PO):
- **Timeout 5 s instead of 3 s.** The kit's drill makes payments answer in exactly 3 s and calls that step "slow". With a 3 s
  timeout it would be marked DOWN and an incident opened, which contradicts "don't confuse latency with failure". Verified in the drill: DEGRADED, no incident.
- **Shorter tiers.** At 30 s, claims' 40 s outage in `--fast` may get only one check, so no incident. At 60 s, quotes could never
  reach "3 failures in 60 s". The PO's 10/30/60/120 s remain valid per-service values.

---

## 3. Research that shaped the rules (full notes in RESEARCH.md)

| Idea | Source | Used for |
|---|---|---|
| Confirm before alerting, confirm before resolving; any failure resets the recovery period | Better Stack confirmation/recovery periods, Uptime Kuma retries | 2-failure / 3-success rules, RECOVERING → DOWN |
| Dedup key: a repeated trigger is appended, not duplicated | PagerDuty Events API | `trigger_key` UNIQUE |
| Triggered → Acknowledged → Resolved; ack on resolved is rejected | PagerDuty | incident lifecycle, 409 |
| Fixed public vocabulary, kept separate from internal detail | Atlassian Statuspage | public labels and templates |
| Reject a timeout ≥ the check interval | Better Stack | config validation |
| MTTA / MTTR definitions | PagerDuty analytics | metrics |

---

## 4. Scope

**Built (all must-haves, backend form):**

| # | Must-have | Delivered as |
|---|---|---|
| 1 | Service catalogue | `GET/POST /api/services`, `GET/PATCH /api/services/{id}` with validation |
| 2 | Scheduled independent checks, every result stored | per-service async loops, `health_checks` table, `GET /api/services/{id}/checks` |
| 3 | Healthy / degraded / down, recovery | pure state machine + flaky detection |
| 4 | Incidents: auto open/close, ack, notes, timeline | `/api/incidents…` |
| 5 | Reliability against target + error budget | `GET /api/services/{id}/metrics` |
| 6 | Operator dashboard | `GET /api/dashboard/summary` (counts, per-service status, latency, availability, budget, incidents) |
| 7 | Public status page | `GET /api/public/status`, `GET /api/public/history` (no login, customer-safe) |
| 8 | Webhook notifications | outbox + sender, visible per incident |
| 9 | Product metrics | `GET /api/metrics/summary` |
| 10 | One-command run | `docker compose up --build` |
| + | Operator login | `/api/auth/*` |

**Left out, with reasons:**
- **UI:** PO decision (D47). A React UI was built and then archived outside the repo.
- **Flaky-triggered incidents:** built, but off by default until the PO answers (D49).
- **Manual incidents:** no PO request.
- **Escalation and on-call:** a product in itself.
- **Subscriptions:** out of scope.
- **Load-balanced checking across instances:** not needed. Only one instance checks (advisory lock, D50).

---

## 5. Roadmap and status

| Phase | Content | Status |
|---|---|---|
| 1 | Discovery, PO questions, research, Gate 1 plan | ✅ 18:00 |
| 2 | Foundation: Postgres, models, seed, catalogue API | ✅ |
| 3 | Core monitoring: classify, state machine, scheduler, flaky | ✅ |
| 4 | Incidents, timeline, ack/notes, webhook outbox | ✅ |
| 5 | Reliability, metrics, public + dashboard APIs, auth | ✅ |
| 6 | Tests (unit, integration, acceptance), drill run, docs | ✅ |
| 6b | Remaining items: flaky-incident option, 7-day window, single-scheduler lock, transcript script | ✅ |
| 7 | PO answers, own-words PROGRESS/REVIEW, final transcript export, commit/push | ⏳ (you) |

---

## 6. Open questions for the product owner

1. May we use a 5 s timeout instead of 3 s? (The drill's 3 s "slow" step would otherwise page as an outage.)
2. May the default tiers be 10/15/20/30 s? (30/60/120 s can't detect the drill's short outage and flakiness.)
3. Should crossing the flaky threshold open an incident (lower severity), or only mark DEGRADED? *(Built as the per-service setting `flaky_opens_incident`, off by default; a "yes" means switching it on.)*
4. Which second trigger should create additional unresolved incidents for one service: flaky, or manual?

---

## 7. Risks we designed for

| Risk | Mitigation |
|---|---|
| The timeout equals the drill's slow response | 5 s timeout, validated `slow < timeout < interval` |
| A duplicate incident for one outage | edge trigger, unique `trigger_key`, RECOVERING → DOWN keeps the same incident |
| Recovery closes unrelated incidents | resolves only `service_down` incidents of that service |
| The internal error (`10.12.4.7`, `*-db`) leaks publicly or in webhooks | separate schemas, template text, leak tests on the public API and webhook payloads |
| A slow service blocks the others; checks overlap | independent loops, hard timeout, semaphore; a sequential loop per service (tested) |
| A webhook failure blocks checking; lost or duplicate webhooks | outbox written in the check transaction, separate sender, unique per event, survives restart |
| A restart loses state | everything in Postgres; tested in an integration test and the live acceptance test |
| A schema upgrade takes an AccessExclusive lock on every start (deadlocks under concurrency) | `init_db` probes `information_schema` and runs `ALTER TABLE` only when the column is genuinely missing (D54) |
| Port 5432 is taken on the laptop | Postgres published on 5433 |
