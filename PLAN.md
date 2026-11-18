# Pulse – Plan (Gate 1)

> Legend: **✅ Decided** (by me) · **⏳ Open**: to resolve while building, logged in DECISIONS.md · **💡 Proposal**: not yet confirmed

---

## 1. Problem in my words

Acko runs many services. When one fails or slows down, nobody finds out in time:

| User | Pain today | Outcome they need |
|---|---|---|
| **On-call engineers** (service owners) | Hear about failures late, often from support | A fast alert, one incident per outage, a timeline, acknowledge and notes |
| **Support team** | Customers call and support has no context | See at a glance what is broken and since when |
| **Customers** | Payment fails or claim is stuck: "is it me or Acko?" | A public status page in plain language, with no internal details |
| **Leads** (secondary) | Can't say how reliable a service really was | Availability against target, and the error budget left |

**The outcome that matters:** detect outages quickly, don't cry wolf (one incident per outage, no leaks), and keep everyone informed until recovery.

---

## 2. Product owner Q&A

The answers below are **my own working decisions**. The PO has not answered these yet. When the PO replies, I will copy the answers word for word into the last column, and log any change in DECISIONS.md.

| # | Question | My decision | PO answer |
|---|---|---|---|
| 1 | How often should Pulse check each service? | Per-service interval, **default 10 s**, allowed **6–60 s**; checks independent | ⏳ not yet asked |
| 2 | What timeout makes a check slow or unhealthy? | **Timeout 5 s**. 2xx ≤ 1 s = good, 2xx > 1 s = slow, non-2xx / connection error / timeout = failed | ⏳ |
| 3 | Does one failed check create an incident? | No. **2 consecutive failures → DOWN + incident**. A success in between resets the count. | ⏳ |
| 4 | How many successes resolve an incident? | **2 consecutive successes** → HEALTHY, incident resolved | ⏳ |
| 5 | Degraded vs down? | **Degraded** = slow 2xx, or 1st failure. **Down** = 2 failures in a row. Degraded alone opens **no** incident. | ⏳ |
| 6 | Multiple open incidents per service? | **No.** At most 1 unresolved incident per service; further failures append to its timeline | ⏳ |
| 7 | Webhook endpoint and payload? | Receiver accepts **POST, any path, any JSON, always 200** (verified in `webhook-receiver/app.py`). Payload designed by me (§6) | ⏳ |
| 8 | Error details for customers? | **Status only**, plus customer-friendly incident text. No errors, IPs, hosts, owners or notes | ⏳ |
| 9 | Availability window? | **Rolling 24 h** (7 days if time allows). Check-based formula (§9) | ⏳ |
| 10 | Priorities if time runs out? | P0/P1/P2 list in §5 | ⏳ |
| 11 | Must history survive restarts? | **Yes.** SQLite on a Docker volume; active incidents and schedules resume after restart | ⏳ |
| 12 | Auth for the operator dashboard? | **Deferred** for the MVP; documented as needed in production | ⏳ |

---

## 3. Research findings (Statuspage, Uptime Kuma, Better Stack, PagerDuty, UptimeRobot)

- **Anti-flapping is standard.** Uptime Kuma puts a monitor in "Pending" and retries before "Down". Better Stack has a confirmation period before opening an incident and a recovery period before resolving, and any failure resets the recovery period. → I adopted 2 failures to open and 2 successes to resolve.
- **Dedup.** In PagerDuty, an event for an already-open incident is appended to it. Ack events for resolved incidents are discarded. → One open incident per service; acknowledging a resolved incident is rejected.
- **Lifecycle.** PagerDuty and Better Stack use Open → Acknowledged → Resolved. → Adopted.
- **Notifications.** PagerDuty notifies on trigger by default. Statuspage SMS goes out only on create and resolve. → Webhooks on opened and resolved only.
- **Public page.** Statuspage keeps component status separate from internal detail. → A fixed public vocabulary.
- **Config validation.** Better Stack rejects a check interval shorter than the timeout. → Validate timeout < interval.
- **Metrics.** PagerDuty: MTTA = created → first ack; MTTR = created → resolved; open incidents excluded. → Adopted, plus MTTD.
- **SLO / error budget.** I found no native per-service SLO with error budget in the four tools I verified. Datadog has one. This is Pulse's point of difference.

---

## 4. Decisions

| Area | Decision |
|---|---|
| Stack | Python **FastAPI** + **SQLite** (WAL mode, file on a Docker volume) + **httpx** async + Jinja2 server-rendered HTML + **pytest** |
| Interval | Default 10 s; validated range 6–60 s |
| Timeout | 5 s (must be < interval) |
| Slow threshold | 1000 ms |
| Healthy | 2xx within ≤ 1000 ms |
| Degraded | 2xx > 1000 ms, **or** first failure |
| Down | **2 consecutive failures** (non-2xx, connection error or timeout) |
| Recovery | **2 consecutive successes** → HEALTHY, incident resolved |
| Incident opens on | **DOWN only** |
| Duplicate incidents | Not allowed: 1 unresolved incident per service |
| Flaky services | **Accepted:** each separate outage opens its own incident. The dashboard shows the failure rate so flakiness is visible. |
| Availability | Rolling **24 h**, check-based; 7 days if time allows |
| Public page | Customer-friendly status only |
| Auth | Deferred for the MVP |
| Webhook | POST JSON to the webhook receiver on **opened** and **resolved** only (path and retry policy ⏳ O-6) |

**Changed from my earlier draft:**
- 3 failures → **2**: faster detection; claims in the `--fast` drill is down for only 40 s.
- Incidents on Down + Degraded → **Down only**: degraded is a warning, not an outage.
- 30-day window → **24 h**: a demo can produce meaningful 24 h numbers; 30 days would be almost empty.
- Considered PostgreSQL → kept **SQLite on a volume**: it meets the "survive restarts" requirement with less setup.
- Timeout 3 s → **5 s**: the drill's `slow 3000ms` returns 200 after ~3 s. With a 3 s timeout it would time out and show **Down**. With 5 s it correctly shows **Degraded**.

### How the drill should behave under these rules (predicted)

| Drill step | Expected Pulse behaviour |
|---|---|
| claims down (40 s fast / 120 s full) | DEGRADED after 1 check → DOWN + incident + webhook after the 2nd (≤ ~20 s) → resolved + webhook after 2 successes on reset |
| payments slow 3 s | DEGRADED, **no incident**, no webhook; back to HEALTHY on reset |
| quotes flaky 50% | Alternates HEALTHY/DEGRADED; **possibly 0..several incidents** (random). The acceptance test asserts consistency, not an exact count. |

### Open questions (decide while building, log in DECISIONS.md)

- **O-1. Slow responses and recovery.** Does a slow 2xx (> 1 s) count as a *success* for the 2-successes recovery rule? It does reset the failure counter. If so, does the service recover to DEGRADED rather than HEALTHY?
- **O-2. Display while recovering.** After the 1st success following DOWN, does the service show "DOWN (recovering)" or DEGRADED? The incident stays open either way.
- **O-3. Availability and slow checks.** Does a slow 2xx count as a successful check in the availability formula?
- **O-4. Error budget units.** Availability is check-based, but the budget is in minutes. Convert as `budget used = failed checks × interval`, or `minutes spent in DOWN`?
- **O-5. A service edited mid-window.** If its interval changes, does it affect the "eligible checks" count?
- **O-6. Webhook path and retries.** Path name (`/pulse` proposed). Retry count and backoff (proposed: 3 attempts, 1/2/4 s).
- **O-7. Out-of-scope candidates** (§5): confirm or reject.

---

## 5. Scope

### Build order (by priority)

| Priority | Feature | Must-have # |
|---|---|---|
| P0 | Service catalogue: API + UI, with validation | 1 |
| P0 | Scheduled health checks (one task per service, every result stored) | 2 |
| P0 | Healthy / Degraded / Down state engine | 3 |
| P0 | Automatic incident open / resolve; acknowledge | 4 |
| P0 | Public status page | 7 |
| P0 | Webhook notifications | 8 |
| P0 | One-command run + README | 10 |
| P1 | Incident timeline and notes | 4 ⚠️ |
| P1 | Reliability: availability + error budget | 5 |
| P1 | Response-time history, operator dashboard | 6 |
| P1 | Product metrics | 9 |
| P2 | Auth, advanced analytics, customer subscriptions | — |

⚠️ Timeline and notes are listed as P1 in my priorities, but **all of must-haves 1–10 are required** for a complete product. P1 means "build second", not "optional". Only P2 items are cut.

### Out of scope

- ✅ **Authentication / RBAC:** not a must-have; listed under "going further". Documented as needed in production.
- ✅ **Customer subscriptions:** need delivery, unsubscribe and abuse protection.
- ✅ **Advanced analytics:** beyond must-have #9.
- 💡 Escalation policies and on-call rotations: a product in themselves.
- 💡 Maintenance windows: would complicate the SLO maths.
- 💡 Non-HTTP and multi-region checks: the kit only exposes HTTP `/health`.

---

## 6. System design

```
                         docker compose (one network)
 ┌────────────────────────────────────────────────────────────────────┐
 │  ┌─────────────────────────── Pulse (FastAPI) ───────────────────┐ │
 │  │                                                               │ │
 │  │  Scheduler: one asyncio task per service ──── GET /health ────┼─┼─► quotes / payments /
 │  │   (own interval, httpx timeout 5 s)                           │ │   claims / notifications
 │  │        │                                                      │ │   (kit, unchanged, :8080)
 │  │        ▼                                                      │ │
 │  │  check_results ─► State engine (PURE fn) ─► Incident manager  │ │
 │  │                      │                          │             │ │
 │  │                      ▼                          ▼             │ │
 │  │                 services.state            Notifier (retry) ───┼─┼─► webhook-receiver:9000/pulse
 │  │                                                               │ │
 │  │  Operator: /  /services  /incidents/{id}  /metrics            │ │
 │  │  Public:   /status        API: /api/...                       │ │
 │  │                                                               │ │
 │  │  SQLite (WAL) on volume pulse-data                            │ │
 │  └───────────────────────────────────────────────────────────────┘ │
 └────────────────────────────────────────────────────────────────────┘
```

- **Isolation:** a separate task and timeout per service, so a slow service never delays the others. Measured through scheduler lag.
- **State engine:** `next_state(current_state, counters, check) → (new_state, counters, events)`. No I/O, so it is fully table-testable.
- **Restart:** state and consecutive counters are persisted on `services`, so on startup Pulse reads them back, reloads open incidents, and restarts every task. Nothing is rebuilt from memory.
- **Catalogue changes** start, restart or stop that service's task.
- **Webhook payload** (internal error text is never included):
  ```json
  {"event": "incident.opened", "incident_id": "INC-001", "service": "payments",
   "owner": "...", "status": "DOWN", "message": "Service health check failed",
   "timestamp": "2026-11-18T12:00:00Z"}
  ```
  `incident.resolved` has the same shape, with `"status": "HEALTHY"`, `"message": "Service recovered"`, and a `duration_s` field.
- **Retry testing:** the receiver always returns 200, so retries are tested by stopping the receiver container, or with a mock in unit tests.

---

## 7. Data model (SQLite)

```
services
  id PK, name UNIQUE, health_url, interval_s (6–60), timeout_ms (< interval),
  owner, slo_target (0–100, e.g. 99.9),
  state (unknown|healthy|degraded|down), state_since,
  consec_failures, consec_successes, created_at, updated_at

check_results
  id PK, service_id FK, scheduled_at, checked_at,   -- lag = checked_at − scheduled_at
  ok BOOL, slow BOOL, status_code NULL, latency_ms NULL,
  error TEXT NULL                                   -- internal only, never rendered publicly
  INDEX (service_id, checked_at)

incidents
  id PK, code ("INC-001"), service_id FK,
  status (open|acknowledged|resolved),
  first_failure_at,                                 -- for MTTD
  opened_at, acknowledged_at NULL, acknowledged_by NULL, resolved_at NULL,
  public_message                                    -- customer-friendly text
  -- rule: at most 1 row per service with status != resolved (enforced in code + partial unique index)

incident_events                                     -- timeline
  id PK, incident_id FK, at,
  type (opened|failure|acknowledged|note|recovering|resolved|notified),
  actor NULL, message

notifications
  id PK, incident_id FK, event (opened|resolved), attempt, sent_at,
  http_status NULL, success BOOL, error NULL
```

---

## 8. Main screens

**Operator dashboard** `/`
```
┌ Pulse – Operator ────────────────────── auto-refresh (💡 5 s) ────┐
│ OPEN INCIDENTS                                                    │
│ ● INC-003 claims   DOWN  since 14:02 (6m)   unacked   [Ack] [View]│
├───────────────────────────────────────────────────────────────────┤
│ Service       State      Latency  Last 20     Fail%  24h     Budget│
│ quotes        ● Degraded   41 ms  ▁▂✕▁✕▂✕    45%   99.31%   0.0m │
│ payments      ● Degraded 3012 ms  ▁▁▇▇▇▇▇     0%   100.0%   1.4m │
│ claims        ● Down         –    ▁▁✕✕✕✕✕   100%   98.90%   0.0m │
│ notifications ● Healthy    38 ms  ▁▁▁▁▁▁▁     0%   100.0%   1.4m │
├───────────────────────────────────────────────────────────────────┤
│ RECENT INCIDENTS  INC-002 quotes 40s resolved · INC-001 ...  [all]│
└───────────────────────────────────────────────────────────────────┘
```

**Incident detail** `/incidents/{id}`
```
INC-003 · claims · DOWN · Acknowledged by <name> · open 6m 12s
Timeline
 14:01:50  check failed (503)                      ← first_failure_at
 14:02:00  incident opened (2 consecutive failures)
 14:02:01  webhook incident.opened → 200
 14:03:00  acknowledged by <name>
 14:04:30  note: "DB failover in progress"
 14:08:00  recovering (1/2 successes)
 14:08:10  resolved (2 consecutive successes) · webhook → 200
[ note ________________ ] [Add note] [Acknowledge]
```

**Public status page** `/status` (no owners, errors, IPs, notes or latencies)
```
Acko System Status                  ● Some systems are affected
Claims          ● Major outage        since 14:02
Payments        ● Degraded performance
Quotes          ● Operational
Notifications   ● Operational
Last 24 h:  claims ▮▮▮▮▮▮▮▮▯▯▮▮   (▮ up / ▯ down / grey = no data)
Recent incidents: "Claims – service disruption – resolved after 6 min"
```

**Catalogue** `/services`: a table plus an add/edit form (name, URL, interval, timeout, owner, target), with validation errors shown inline.

**Metrics** `/metrics` (HTML) and `/api/metrics` (JSON), see §10.

---

## 9. Reliability formula

- **Availability (24 h)** = successful checks ÷ eligible checks × 100.
  - Eligible = checks actually performed in the window. Time before monitoring began is **not** assumed healthy; it is shown as "no data".
  - Whether slow checks count as successful: ⏳ O-3.
- **Allowed downtime** = window minutes × (1 − target). For 99.9% over 24 h: **1.44 min**.
- **Budget remaining** = allowed − observed downtime (unit ⏳ O-4), shown in minutes and %. It can go negative, shown as "exhausted".
- *Note:* at 99.9% / 24 h, a single 20 s outage uses about 23% of the budget, so the drill will visibly exhaust it. That is good for the demo.

---

## 10. Test plan (pytest)

**Unit**
- State engine, table-driven:
  - 2xx ≤ 1 s → HEALTHY
  - 2xx > 1 s → DEGRADED
  - 1 failure → DEGRADED, no incident
  - 2 failures in a row → DOWN + open
  - F, S, F → never DOWN (the counter resets)
  - DOWN, S → still open (recovering); DOWN, S, S → HEALTHY + resolve
  - DOWN, S, F → stays DOWN, no new incident
  - timeout → failure
  - O-1 cases once decided
- Incidents:
  - a second DOWN while one is open → no new incident, timeline event added
  - ack on a resolved incident → rejected
  - acknowledging twice is idempotent
- Availability and budget:
  - hand-computed (8553 / 8640 = 98.99%)
  - no data → "no data", not 100%
  - a service added mid-window
  - budget exhausted shows as negative
- Catalogue validation: unique name, interval within 6–60, timeout < interval, target within (0, 100]
- Public view model: contains only whitelisted fields

**Integration**
- Catalogue CRUD via the API (FastAPI TestClient, temp SQLite)
- **Scheduler isolation:** one stub service delaying 4 s; the others' scheduler lag stays < 1 s
- Restart: create an open incident, restart the app → the incident is still open, counters are restored, checks resume
- Notifier: exactly 1 opened + 1 resolved per incident against the real receiver (`GET /api/received`); retry when the receiver is unreachable; every attempt recorded
- **Leak test:** with a service down:
  - the `/status` HTML does **not** contain `10.12.4.7`, `timeout`, `-db`, owner names or note text;
  - webhook bodies do **not** contain `10.12.4.7` or `-db`. Webhooks are an internal channel, so the owner **is** included on purpose.

**Acceptance**
- Script: `docker compose up`, `DELETE /api/received`, run `incident-drill.sh --fast`, then assert:
  - claims: exactly 1 incident, opened and resolved, with 2 matching webhooks
  - payments: was DEGRADED, 0 incidents
  - quotes: every incident opened has been resolved, and webhooks = 2 × its incidents
  - all services HEALTHY at the end
  - `/status` has no leaks

---

## 11. Metrics (how I will know Pulse works)

| Metric | Definition | Target |
|---|---|---|
| **MTTD** | `opened_at − first_failure_at` | ≤ 1 interval (~10 s) |
| **MTTA** | `acknowledged_at − opened_at` (unacknowledged excluded) | shown |
| **MTTR** | `resolved_at − opened_at` (open excluded) | shown |
| Incidents | count per service, last 24 h | shown |
| Check success rate | ok ÷ total, per service | shown |
| Check latency | p50 / p95 per service | shown |
| **Scheduler lag** | p95 of `checked_at − scheduled_at` | < 1 s even while payments is slow |
| Webhook delivery | success %, average attempts | 100% eventually delivered |
| Public leaks | leak-test result | 0 |

**Demo success criteria:**
- The `--fast` drill produces exactly the predicted claims and payments behaviour.
- No duplicate incidents.
- Scheduler lag stays under 1 s.
- Zero leaks on the public page and in webhooks.
