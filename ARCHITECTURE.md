# Pulse – System Design and Architecture (backend)

Pulse is a **backend-only modular monolith** (D47): one FastAPI process and one PostgreSQL database. It runs next to the
**unchanged** starter kit (4 mock services and the webhook receiver) in a single `docker compose` network. Health
checks and webhook delivery run as background asyncio tasks inside the same process. People use Pulse through its
REST API: the interactive Swagger UI at `/docs`, or `curl`.

> Plan: DESIGN.md · Decisions: DECISIONS.md (D1–D47) · How to test each requirement: TESTING.md · Gate 1 plan: PLAN.md (frozen)

---

## 1. Context

```
                    ┌──────────────────────────── docker compose network ────────────────────────────┐
  Customer apps ───►│  ┌────────────────────────────── pulse :8000 ──────────────────────────────┐   │
  (status page)     │  │                                                                         │   │
                    │  │  REST API   /api/public/*            public, customer-safe, no login    │   │
  Engineers ───────►│  │             /api/auth/*              login → signed session cookie      │   │
  (curl, /docs)     │  │             /api/services, /api/incidents, /api/dashboard, /api/metrics │   │
                    │  │             /healthz, /docs                                             │   │
                    │  │                                                                         │   │
                    │  │  Background  Scheduler ── GET /health (timeout, ≤10 at once) ─────────────────► quotes:8080
                    │  │  (asyncio)     one loop per service, own interval                       │   │   payments:8080
                    │  │              Notifier ── POST /pulse (outbox, 4 attempts) ──────────────────► claims:8080
                    │  │                                                                         │   │   notifications:8080
                    │  │  Domain      classify · next_state · reliability · messages · validation│   │   webhook-receiver:9000
                    │  │  (pure)                                                                 │   │
                    │  └──────────────────────────────────┬──────────────────────────────────────┘   │
                    │                                     │ SQLAlchemy 2.0 + psycopg 3                │
                    │                            ┌────────▼─────────┐                                 │
                    │                            │ postgres:16      │  volume pgdata                  │
                    │                            └──────────────────┘                                 │
                    └─────────────────────────────────────────────────────────────────────────────────┘
```

| Container | Source | Host port → container | Role |
|---|---|---|---|
| `pulse` | `./Dockerfile` (Python 3.12) | 8000 → 8000 | REST API, scheduler, notifier |
| `db` | `postgres:16-alpine` | 5433 → 5432 | all persistent data |
| `quotes`, `payments`, `claims`, `notifications` | starter kit (unchanged) | 7001–7004 → 8080 | monitored services |
| `webhook-receiver` | starter kit (unchanged) | 9900 → 9000 | stands in for chat, email and paging |

---

## 2. Components

The code is in layers. Each layer only calls the layers below it.

```
 app/routes/      api.py (operator + auth) · public.py · deps.py         ← HTTP: parse, authorise, call down
       │
 app/             catalog.py · incidents.py · reporting.py · auth.py     ← application logic (uses the DB)
 app/monitor/     scheduler.py · probe.py · check_processor.py           ← background checking
 app/notifier.py                                                         ← background webhook delivery
       │
 app/domain/      classify.py · state.py · reliability.py · messages.py · validation.py   ← PURE rules, no I/O
       │
 app/             models.py · db.py · seed.py · config.py                ← persistence and settings
```

| # | Component | File | Responsibility | Key design choice |
|---|---|---|---|---|
| 1 | Service catalogue | `catalog.py`, `seed.py` | create and update services; seed the 4 kit services | a PATCH is merged with stored values, then the whole result is validated |
| 2 | Tiered scheduler | `monitor/scheduler.py` | one loop per enabled service, on its own interval | a sequential loop per service means no overlap; a supervisor adds and removes loops |
| 3 | Health-check engine | `monitor/probe.py` | async GET with a hard timeout; measures the response time | never raises: every failure becomes a result |
| 4 | Outcome classifier | `domain/classify.py` | OK_FAST · OK_SLOW · FAIL_HTTP · FAIL_TIMEOUT · FAIL_CONNECTION | pure |
| 5 | State manager | `domain/state.py` | HEALTHY / DEGRADED / DOWN / RECOVERING / UNKNOWN | pure `next_state()` returns the actions to take |
| 6 | Flaky detection | `monitor/check_processor.py` | X failures in Y s → DEGRADED (`flaky`) | counted in SQL, so it survives restarts; failures while DOWN are ignored |
| 7 | Check processor | `monitor/check_processor.py` | store the check → state → incidents → outbox, in **one transaction** | service row locked `FOR UPDATE` |
| 8 | Incident manager | `incidents.py` | open, resolve, ack, notes, timeline events | dedup by `UNIQUE(trigger_key)` |
| 9 | Notification manager | `notifier.py` | deliver outbox rows to the webhook receiver | at-least-once, bounded retries, history per row |
| 10 | Reliability calculator | `domain/reliability.py`, `reporting.py` | availability, error budget, latency p50/p95 | "insufficient data", never a made-up 100 % |
| 11 | Metrics | `reporting.py` | MTTD, MTTA, MTTR, success rate, lag, duration, webhook rate, flaky episodes | computed from stored rows |
| 12 | Public view | `reporting.py`, `routes/public.py` | customer-safe status and history | only `Public*` schemas; text from fixed templates |
| 13 | Auth | `auth.py` | one operator account, signed HttpOnly cookie | `require_operator` on every operator route |
| 14 | Flaky incidents (optional) | `domain/state.flaky_incident_action`, `incidents.open/resolve_flaky_incident` | second, independent incident trigger when `flaky_opens_incident` is on | off by default (PO); lower severity; resolves when the window clears |
| 15 | Single-scheduler lock | `db.try_acquire_scheduler_lock`, `main.py` | only one instance runs the scheduler and notifier | Postgres advisory lock on a detached connection; others are `standby` |

---

## 3. Key flows

### 3.1 One health check

```
Scheduler loop (service S), every check_interval_seconds
  │ read config from DB (a PATCH takes effect on the next cycle)
  │ async with semaphore:  probe(url, timeout) ──GET /health──► mock service
  ▼
process_check()   ONE transaction, services[S] locked
  1. classify(http_status, ms, error_kind, slow_threshold)      → outcome
  2. INSERT health_checks (scheduled/started/completed, outcome, http_status, ms, error)
  3. flaky = COUNT(failed checks in last Y s, not while DOWN) ≥ X
  4. next_state(prev, counters, outcome, flaky, rules)          → Transition
  5. UPDATE services; INSERT service_state_changes if status/reason changed
  6. open_incident / recovery_progress / recovery_interrupted / resolve_incidents
     → incidents + timeline events + outbox rows
COMMIT, then sleep until the next due time (no catch-up burst)
```

### 3.2 State machine and incidents

```
 UNKNOWN/HEALTHY ─fail─► DEGRADED ─fail─► DOWN ─────────── open incident (once, trigger_key = down:{svc}:{first failed check})
        ▲   ▲               │ ok            │ ok
        │   └──── ok ───────┘               ▼
        │                              RECOVERING 1/3 ─ok─► 2/3 ─ok─► HEALTHY (or DEGRADED if slow/flaky)
        │                                   │ fail                    └─ resolve this service's service_down incidents
        │                                   ▼
        │                                 DOWN (same incident, "recovery_interrupted")
 slow 2xx → DEGRADED (slow_response) · X failures in Y s → DEGRADED (flaky), never overrides DOWN/RECOVERING

 Incident: OPEN ──ack──► ACKNOWLEDGED ──confirmed recovery──► RESOLVED   (ack/notes on RESOLVED → 409)
```

### 3.3 Webhooks

```
check transaction ── INSERT notifications (PENDING, payload, next_attempt_at=now) ── COMMIT
Notifier every 2 s ── due PENDING rows ── POST http://webhook-receiver:9000/pulse
   2xx → DELIVERED (+ "notified" event) · timeout/conn/5xx/429 → retry after 5, 15, 45 s
   after 4 attempts or any other 4xx → FAILED (+ "notify_failed" event)
```
Payload: `event_id, event, incident_id (INC-0001), service, severity, status, message, started_at, timestamp`, and
`resolved_at, duration_seconds` on resolve. It carries the error *category* ("HTTP 503"), never the raw error text.

### 3.4 Restart

All state is in Postgres. On boot: `create_all` (adds missing tables only) → seed only if empty → scheduler restarts the loops →
counters continue, open incidents stay open, pending webhooks are retried. The time Pulse was down is **missing data**.

---

## 4. Data model

```
services 1─* health_checks · 1─* service_state_changes · 1─* incidents
incidents 1─* incident_events (timeline, is_public) · 1─* incident_notes · 1─* notifications (outbox)
```

| Table | Key columns and constraints |
|---|---|
| `services` | config (criticality, interval, timeout, slow, failure/recovery thresholds, flaky X/Y, target, enabled) + state (status, reason, since, counters, last_*). CHECK `slow < timeout < interval`, criticality ∈ tiers, 0 < target < 100 |
| `health_checks` | scheduled/started/completed_at (lag, duration), outcome, success, http_status, response_time_ms, error_message (operator only), resulting_status. INDEX (service_id, started_at) |
| `service_state_changes` | from/to status, reason, changed_at (flaky episodes) |
| `incidents` | trigger_type, **trigger_key UNIQUE**, severity, status, public_message, first_failure_at, opened/acked/resolved. **No** one-open-per-service rule |
| `incident_events` | event_type, description, is_public, metadata JSONB |
| `incident_notes` | author, note (internal only) |
| `notifications` | event_type, payload JSONB, delivery_status, attempts, next/last attempt, last status/error. **UNIQUE (incident_id, event_type)** |

---

## 5. API

| Area | Endpoint | Access |
|---|---|---|
| Health | `GET /healthz`, `GET /api/health` (incl. `scheduler`: active / standby / disabled) | public |
| Public status | `GET /api/public/status`, `GET /api/public/history?days=7` | public, customer-safe |
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | – |
| Catalogue | `GET/POST /api/services`, `GET/PATCH /api/services/{id}` | operator |
| Checks / reliability | `GET /api/services/{id}/checks?limit=` (alias `/history`), `GET /api/services/{id}/metrics?window_hours=24` or `168` | operator |
| Incidents | `GET /api/incidents?status=active\|open\|acknowledged\|resolved&service_id=`, `GET /api/incidents/{id}`, `POST /api/incidents/{id}/acknowledge`, `POST /api/incidents/{id}/notes` | operator |
| Dashboard | `GET /api/dashboard/summary` | operator |
| Metrics | `GET /api/metrics/summary` | operator |
| Docs | `GET /` → `/docs` (Swagger UI), `/openapi.json` | public |

Errors: 401 (not logged in), 404 (unknown id), 409 (ack or note on a resolved incident), 422 (invalid config, with every rule that failed).

---

## 6. PO requirements → code → proof

| PO requirement | Implementation | Automated proof |
|---|---|---|
| R1 tiers, all configurable | `config.TIER_INTERVAL_SECONDS`, `catalog.py`, PATCH | `test_skeleton`, `test_api::test_list_and_create…`, `test_patch_is_validated…` |
| R2 timeout and latency classes | `classify.py`, `probe.py` | `test_state::test_classify`, `test_probe_classifies_timeout…` |
| R3 2 failures → DOWN + incident | `state.py` | `test_state`, `test_full_outage_lifecycle`, acceptance 3, 4 |
| R4 3 successes to recover | `state.py` | `test_recovery_needs_three…`, acceptance 5 |
| R5 flaky (+ optional incident, D49) | `check_processor._failures_in_window`, `flaky_incident_action` | `test_flaky_*`, acceptance 6 |
| R6 multiple incidents, no duplicates | `incidents.py`, `UNIQUE(trigger_key)` | `test_repeated_failures_never…`, `test_recovery_resolves_only…`, acceptance 7 |
| R7 webhooks | `notifier.py`, outbox | 6 notifier tests, acceptance 8 |
| R8 public vs operator | `schemas.py`, `messages.py` | `test_public_views_never_expose…`, acceptance 10 |
| R9 availability and budget (24 h, 7 d) | `reliability.py`, `reporting.py`, `?window_hours=168` | `test_domain_misc`, `test_seven_day_window` |
| R11 Postgres, restart | `db.py`, `main.py` | `test_state_and_incidents_survive…`, acceptance 9 |
| R12 auth | `auth.py` | `test_operator_api_requires_login` (12 routes) |

---

## 7. Quality attributes

| Concern | How |
|---|---|
| Isolation | async loop per service, hard timeout, semaphore(10), DB work in threads. Drill: scheduler lag p95 ≈ 10 ms |
| No overlap | each loop awaits its own check (tested: max 1 in flight per service) |
| Consistency | one transaction per check, row locks, DB constraints back up the app rules |
| Security | public schemas can't carry error text; operator routes need a session; constant-time password compare; demo credentials only via env |
| Testability | rules are pure functions; probe and HTTP transport are injectable |

## 8. Known limitations

- Only one instance runs checks (advisory lock, D50). Extra instances are API-only standbys; checking is not spread across instances.
- Availability is sampled: it can miss outages shorter than the interval.
- `create_all` plus a small idempotent column-upgrade list instead of migrations (D52); Alembic needed as the schema grows.
- One shared operator account; no roles; no login rate limiting.
- No data-retention job (about 15k checks a day).
- Flaky incidents are off by default (PO Q3 pending); no manual incidents.
