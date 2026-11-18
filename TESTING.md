# How to test Pulse

This file explains how to verify **every requirement** in the problem statement and the product owner's answers:
- automatically, with the test suites;
- by hand, with copy-paste commands against the running stack, for demos and reviews.

| Level | Command | Needs | Time |
|---|---|---|---|
| Unit + integration (121 tests) | `pytest` | Postgres container only | ~110 s |
| Acceptance (scenarios 1–10, 7 tests) | `pytest -m acceptance` | full stack running | ~2–3 min |
| Manual / demo | the `curl` recipes below | full stack running | as needed |
| Drill | `bash pulse-starter-kit/scripts/incident-drill.sh --fast` | full stack running | ~3 min |

---

## 0. Setup

```bash
# 1. Start everything (kit + Postgres + Pulse). Fresh start: docker compose down -v first.
docker compose up -d --build

# 2. Python environment for the tests (Windows paths shown; on Linux/macOS use .venv/bin/...)
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

**Shell helpers for the manual recipes.** Use Git Bash on Windows, or any bash. Paste these once per terminal:

```bash
P=http://localhost:8000
JAR=/tmp/pulse-cookies.txt
# log in once; the session cookie is stored in $JAR
curl -s -c $JAR -H 'Content-Type: application/json' -d '{"username":"operator","password":"pulse-demo"}' $P/api/auth/login; echo
pc()  { curl -s -b $JAR -H 'Content-Type: application/json' "$@"; echo; }          # operator call
kit() { curl -s -X POST "http://localhost:$1/control/$2${3:+?$3}"; echo; }           # kit control: kit 7003 down
sid() { pc $P/api/services | python -c "import sys,json;print(next(s['id'] for s in json.load(sys.stdin) if s['name']=='$1'))"; }
st()  { pc $P/api/services/$(sid $1) | python -c "import sys,json;s=json.load(sys.stdin);print(s['name'],s['current_status'],s['status_reason'],'fails',s['consecutive_failures'],'oks',s['consecutive_successes'])"; }
watch_st() { for i in $(seq ${2:-15}); do st $1; sleep 2; done; }                     # watch_st claims 20
```

Kit ports: quotes **7001**, payments **7002**, claims **7003**, notifications **7004**, webhook receiver **9900**.
Everything is also clickable in Swagger at http://localhost:8000/docs. Call `POST /api/auth/login` there first.

**Tip: make manual tests fast.** The default intervals are 10–30 s. For a demo you can make one service fast through the API
(this is also a test of live config changes), and put it back afterwards:
```bash
pc -X PATCH $P/api/services/$(sid claims) -d '{"check_interval_seconds":3,"timeout_ms":2000,"slow_threshold_ms":800,"flaky_window_seconds":30}'
# restore defaults later:
pc -X PATCH $P/api/services/$(sid claims) -d '{"check_interval_seconds":15,"timeout_ms":5000,"slow_threshold_ms":1000,"flaky_window_seconds":90}'
```

---

## 1. Automated tests

```bash
docker compose up -d db                        # unit/integration tests use the real Postgres on :5433
.venv/Scripts/python -m pytest                 # unit + integration
.venv/Scripts/python -m pytest -m acceptance   # needs the full stack; restarts the pulse container once (scenario 9)
.venv/Scripts/python -m pytest tests/unit -v   # just the business rules, no DB, < 1 s
```

**Test database note (D55):** each pytest session creates its own throwaway database
(`pulse_test_<uid>`) on the Compose Postgres and drops it at the end, so two overlapping test
runs can never race each other's `drop_all`/`create_all`. Set `PULSE_TEST_DATABASE_URL` to force
a specific database (CI does this to reuse one database).

| Suite | File | What it proves |
|---|---|---|
| Unit | `tests/unit/test_state.py` | outcome classification; every state transition; sequences (F→DEGRADED, F,F→DOWN+incident, F,S,F never DOWN, 3 successes to recover, failure during recovery → same incident, slow successes, configurable thresholds) |
| Unit | `tests/unit/test_domain_misc.py` | availability %, "insufficient data", allowed downtime (99.9 %/24 h = 1.44 min), error budget, percentiles, config validation rules, public wording is safe, webhook payload shape |
| Integration | `tests/test_skeleton.py` | schema, seeding with tiers, DB constraints (bad config rejected, several open incidents allowed, duplicate trigger rejected), `/healthz` |
| Integration | `tests/integration/test_check_processing.py` | check → state → incident → outbox in Postgres; no duplicates; interrupted recovery; new outage = new incident; recovery resolves only `service_down` incidents; slow; flaky; flaky window expiry; ack/notes; no internal error in webhooks |
| Integration | `tests/integration/test_notifier_and_scheduler.py` | webhook delivered / retried / backoff / gives up after 4 / 4xx not retried / unreachable; probe timeout and connection errors; **a slow service doesn't delay the others and never overlaps itself** |
| Integration | `tests/integration/test_remaining_features.py` | flaky incidents off by default / on: lower severity, webhook, resolves when the window clears, **coexists with a down incident** (PO R6); 7-day window; only one instance holds the scheduler lock and takeover after a crash; standby instance; column upgrade on an old DB |
| Integration | `tests/integration/test_api.py` | 401 on all 12 operator routes; login/logout; catalogue create/validate/patch; incident API (ack, notes, 409, 404); dashboard + metrics; **public API leak test**; restart keeps state; `/` → `/docs` |
| Acceptance | `tests/acceptance/test_acceptance.py` | scenarios 1–10 against the real kit and webhook receiver (table in §2) |

---

## 2. The ten acceptance scenarios (manual recipes)

Each recipe: **do** → **expect** → *automated by*. Use the fast-interval tip above for quicker results.

### Scenario 1: all services healthy
```bash
for p in 7001 7002 7003 7004; do kit $p reset; done
for s in quotes payments claims notifications; do st $s; done
```
**Expect:** all `HEALTHY`, `fails 0`. *Automated:* `test_1_all_services_healthy`.

### Scenario 2: a slow service is DEGRADED, not DOWN
```bash
kit 7002 slow ms=3000          # payments answers after 3 s (slow threshold 1 s < 3 s < timeout 5 s)
watch_st payments 10
pc "$P/api/incidents?service_id=$(sid payments)&status=active"
kit 7002 reset
```
**Expect:** `DEGRADED slow_response`; the incident list stays `[]`; no webhook. *Automated:* `test_2_slow…`, `test_slow_service_is_degraded_without_incident`.

### Scenario 3: one failure is DEGRADED, no incident
```bash
kit 7003 down; sleep 4; st claims; kit 7003 reset   # with the 3 s fast interval: exactly one failed check
```
**Expect:** `DEGRADED http_error fails 1`, then back to `HEALTHY`, no incident. *Automated:* `test_3_single_failure…`, `test_first_failure_is_degraded_without_incident`.

### Scenario 4: two consecutive failures → DOWN + incident
```bash
kit 7003 down
watch_st claims 6
pc "$P/api/incidents?status=active"
```
**Expect:** `DEGRADED` (fails 1) → `DOWN` (fails 2). One incident `INC-xxxx`, status `OPEN`, severity `SEV2` (claims is high tier).
More failures do **not** add incidents. *Automated:* `test_4_5_8_10…`, `test_two_consecutive_failures…`, `test_repeated_failures_never_create_duplicate…`.

### Scenario 5: recovery needs three consecutive successes
```bash
kit 7003 reset
watch_st claims 8
pc "$P/api/incidents?service_id=$(sid claims)" | python -m json.tool | grep -E '"code"|"status"' | head -4
```
**Expect:** `RECOVERING oks 1` → `RECOVERING oks 2` → `HEALTHY oks 3`; only then is the incident `RESOLVED`.
Variant: run `kit 7003 down` again during RECOVERING. Expect `DOWN`, the **same** incident, and a `recovery_interrupted` event in its timeline.
*Automated:* `test_recovery_needs_three…`, `test_failure_during_recovery…`, `test_interrupted_recovery_keeps_the_same_incident`.

### Scenario 6: flaky service → DEGRADED (reason `flaky`), no incident
```bash
pc -X PATCH $P/api/services/$(sid quotes) -d '{"check_interval_seconds":3,"timeout_ms":2000,"slow_threshold_ms":800,"flaky_window_seconds":30,"failure_threshold":5}'
kit 7001 flaky rate=0.5
watch_st quotes 20                      # wait until the reason shows "flaky"
kit 7001 reset
pc -X PATCH $P/api/services/$(sid quotes) -d '{"check_interval_seconds":20,"timeout_ms":5000,"slow_threshold_ms":1000,"flaky_window_seconds":120,"failure_threshold":2}'
```
**Expect:** status `DEGRADED`, reason `flaky` once 3 failures fall within 30 s. No incident has a flaky trigger.
(`failure_threshold: 5` is set only so that two failures in a row don't turn this into a DOWN incident.)
With default settings, flakiness *can* also produce a normal DOWN incident when 2 failures happen in a row; that is expected.
*Automated:* `test_6_flaky…`, `test_flaky_service_is_degraded_with_reason_and_no_incident`, `test_flaky_window_expires`.

### Scenario 7: multiple incidents for one service, no duplicates
```bash
kit 7003 down;  sleep 15; kit 7003 reset; sleep 15     # outage 1 + recovery (fast interval)
kit 7003 down;  sleep 15                               # outage 2 (keep failing)
pc "$P/api/incidents?service_id=$(sid claims)" | python -m json.tool | grep -E '"code"|"status"|"trigger_key"'
kit 7003 reset
```
**Expect:** exactly one new incident per outage, each with a different `trigger_key` (`down:<service>:<first failed check id>`). Long outages never create extras.
The DB allows several **unresolved** incidents per service (PO requirement). A recovery resolves only that service's
`service_down` incidents. *Automated:* `test_7_multiple_incidents…`, `test_recovery_resolves_only_service_down_incidents`, `test_db_allows_several_unresolved…`.

### Scenario 8: webhooks reach the receiver
```bash
curl -s -X DELETE localhost:9900/api/received
kit 7003 down; sleep 12; kit 7003 reset; sleep 15
curl -s localhost:9900/api/received | python -m json.tool
```
Or open http://localhost:9900 in a browser.
**Expect:** exactly one `incident.opened` and one `incident.resolved` for the incident, POSTed to `/pulse`, e.g.
`{"event":"incident.opened","incident_id":"INC-0003","service":"claims","severity":"SEV2","status":"DOWN","message":"claims failed 2 consecutive health checks (HTTP 503)",...}`.
There is no `10.12.4.7` in any body. Delivery status per incident: `pc $P/api/incidents/<id>` → `notifications[]`.

**Retry / failure handling** (the receiver always answers 200, so stop it to test failures):
```bash
docker compose stop webhook-receiver
kit 7003 down; sleep 12
pc $P/api/incidents/<id> | python -m json.tool | grep -A6 notifications    # PENDING, attempts 1..n, last_error ConnectError
docker compose start webhook-receiver                                      # within ~65 s → DELIVERED on the next retry
kit 7003 reset
```
Health checks keep running the whole time (`st claims` keeps changing): webhook failures never block checking.
**Giving up:** leave the receiver stopped for ~70 s and the incident's `notifications[]` shows 4 attempts
(5/15/45 s backoff) and a final `FAILED`/last_error — no infinite retrying. *Automated:* `test_gives_up_after_max_attempts`.

*Automated:* `test_4_5_8_10…`, `test_retried_after_server_errors…`, `test_gives_up_after_max_attempts`, `test_unreachable_receiver_is_retried`, `test_client_error_is_not_retried`.

### Scenario 9: restart keeps history and open incidents
```bash
kit 7003 down; sleep 12
pc "$P/api/incidents?status=active"                       # note the incident id
pc "$P/api/services/$(sid claims)/checks?limit=1000" | python -c "import sys,json;print(len(json.load(sys.stdin)),'checks')"
docker compose restart pulse; sleep 8
curl -s -c $JAR -H 'Content-Type: application/json' -d '{"username":"operator","password":"pulse-demo"}' $P/api/auth/login
pc "$P/api/incidents?status=active"                       # same incident, still open
st claims                                                 # still DOWN, counters kept
kit 7003 reset                                            # recovery after the restart resolves it
```
Also `docker compose down && docker compose up -d`: the data stays because it's on the `pgdata` volume. Only `down -v` wipes it.
*Automated:* `test_9_restart…`, `test_state_and_incidents_survive_an_app_restart`.

### Scenario 10: the public view is customer-safe
```bash
kit 7003 down; sleep 12
curl -s $P/api/public/status | python -m json.tool          # no login needed
curl -s $P/api/public/status $P/api/public/history | grep -cE "10\.12|claims-db|upstream|503|claims-team"   # expect 0
pc $P/api/incidents/<id> | grep -c "10.12.4.7"                                                             # operators: ≥ 1
kit 7003 reset
```
**Expect:** the public view shows `"status": "Service disruption"` and *"Claims is currently unavailable for some customers. Our engineering
team is investigating the issue."* plus a latest update. It never shows error text, HTTP codes, IPs, owners or notes. The operator
incident shows the exact error `upstream claims-db timeout at 10.12.4.7:5432`.
*Automated:* `test_public_views_never_expose_internal_details`, `test_webhook_payload_has_no_internal_error_text`, acceptance `test_4_5_8_10…`.

---

## 3. Other must-haves and PO requirements

### Service catalogue (must-have 1, PO R1)
```bash
pc $P/api/services | python -m json.tool | head -40          # 4 seeded services with tier defaults
pc -X POST $P/api/services -d '{"name":"policies","health_url":"http://quotes:8080/health","owner":"policy-team","criticality":"high"}'
pc -X POST $P/api/services -d '{"name":"bad","health_url":"http://x/h","owner":"o","check_interval_seconds":3,"timeout_ms":5000}'
pc -X PATCH $P/api/services/$(sid payments) -d '{"check_interval_seconds":4}'
```
**Expect:**
- `policies` is created with `check_interval_seconds: 15` (high tier) and status `UNKNOWN`. It becomes `HEALTHY` after its first check, which shows that new services are picked up without a restart.
- `bad` → **422** "timeout_ms must be shorter than the check interval".
- The PATCH → **422**, because the stored 5 s timeout would no longer fit a 4 s interval: updates are validated as a whole.

Tier defaults (critical 10 s, high 15 s, medium 20 s, low 30 s) come from `criticality`, never from the service name.

### Independent, isolated checks (must-have 2)
```bash
kit 7002 slow ms=4000                                         # payments: every check takes 4 s
sleep 30; pc $P/api/metrics/summary | python -m json.tool | grep -E "lag|duration"
pc "$P/api/services/$(sid notifications)/checks?limit=5" | python -m json.tool | grep started_at
kit 7002 reset
```
**Expect:** `scheduler_lag_p95_ms_1h` stays in the tens of ms, and other services keep their normal spacing. Each health check is stored with
`scheduled_at / started_at / completed_at`. *Automated:* `test_slow_service_does_not_delay_others_and_never_overlaps_itself`.

### Timeouts and outcome classes (PO R2)
```bash
kit 7002 slow ms=6000; sleep 25; pc "$P/api/services/$(sid payments)/checks?limit=3" | python -m json.tool | grep -E "outcome|error_message"; kit 7002 reset
```
**Expect:** `FAIL_TIMEOUT` with "No response within 5000 ms". Over the timeout counts as a failure; between 1 s and 5 s is `OK_SLOW`.
The other classes are `OK_FAST`, `FAIL_HTTP` (503/500) and `FAIL_CONNECTION`:

```bash
docker compose stop notifications; st notifications    # connection refused → FAIL_CONNECTION, DEGRADED
sleep 20; docker compose start notifications; sleep 45; st notifications   # HEALTHY again after 3 successes
```

### Incidents: acknowledge, notes, timeline (must-have 4)
```bash
kit 7003 down; sleep 12
ID=$(pc "$P/api/incidents?status=active" | python -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")
pc -X POST $P/api/incidents/$ID/acknowledge | python -m json.tool | grep -E '"status"|acknowledged_by'
pc -X POST $P/api/incidents/$ID/notes -d '{"note":"DB failover in progress"}' > /dev/null
pc $P/api/incidents/$ID | python -m json.tool | grep -E '"kind"|"text"'
kit 7003 reset; sleep 20
pc -X POST $P/api/incidents/$ID/acknowledge                  # → 409 "Incident is already resolved"
```
**Expect:**
- Status becomes `ACKNOWLEDGED` by `operator`.
- The timeline reads (verified): detected → opened → public_update → notified → acknowledged → note → recovering → public_update → recovering → resolved → public_update → notified.
- After resolution, ack and notes return **409**.

### Reliability and error budget (must-have 5, PO R9)
```bash
pc $P/api/services/$(sid claims)/metrics | python -m json.tool
```
**Expect:**
- `availability.percent` = successful ÷ total checks in 24 h, with `total_checks`, `covered_minutes` and `sufficient` (false under 10 checks, never a made-up 100 %).
- `error_budget.allowed_minutes` = covered minutes × (1 − target); `used_minutes`; `remaining_percent`; `exhausted`.
- p50/p95 latency and flaky episodes.

Checking the maths by hand: 99.9 % over a full day allows 1.44 min (`test_allowed_downtime_for_three_nines…`).

### Operator dashboard (must-have 6), backend form
```bash
pc $P/api/dashboard/summary | python -m json.tool | head -60
```
**Expect:** status counts, per service (status, reason, counters, last check, last 30 response times, availability, budget), active and recent incidents.

### Product metrics (must-have 9)
```bash
pc $P/api/metrics/summary | python -m json.tool
```
**Expect:**
- MTTD (first failed check → incident), MTTA, MTTR (unresolved/unacknowledged excluded).
- 24 h check success rate and average response time.
- Scheduler lag p95 and check duration (is Pulse keeping up?).
- Webhook delivered/failed/pending and success %, plus flaky episodes.

### Authentication (PO R12)
```bash
curl -s -o /dev/null -w "%{http_code}\n" $P/api/services                 # 401 without a cookie
curl -s -o /dev/null -w "%{http_code}\n" $P/api/public/status            # 200, public
curl -s -H 'Content-Type: application/json' -d '{"username":"operator","password":"wrong"}' $P/api/auth/login   # 401
pc -X POST $P/api/auth/logout; pc $P/api/services                        # 401 again
```
*Automated:* `test_operator_api_requires_login` checks every operator route.

### One-command run (must-have 10)
```bash
docker compose down -v && docker compose up --build -d && sleep 20 && curl -s $P/healthz
```
**Expect:** `{"status":"ok","db":"ok","services":4}`, with all four services `HEALTHY` within ~30 s.

---

### Flaky incidents (PO Q3 option, D49) and two unresolved incidents at once (PO R6)
```bash
Q=$(sid quotes)
pc -X PATCH $P/api/services/$Q -d '{"check_interval_seconds":3,"timeout_ms":2000,"slow_threshold_ms":800,"flaky_window_seconds":60,"failure_threshold":5,"flaky_opens_incident":true}'
kit 7001 flaky rate=0.5; sleep 25
pc "$P/api/incidents?service_id=$Q&status=active" | python -m json.tool | grep -E '"code"|trigger_type|severity'
kit 7001 down; sleep 20                                   # now a real outage as well
pc "$P/api/incidents?service_id=$Q&status=active" | python -m json.tool | grep -E '"code"|trigger_type'
kit 7001 reset; sleep 90                                  # outage resolves first, flaky one when the window clears
pc "$P/api/incidents?service_id=$Q" | python -m json.tool | grep -E '"code"|trigger_type|"status"'
pc -X PATCH $P/api/services/$Q -d '{"check_interval_seconds":20,"timeout_ms":5000,"slow_threshold_ms":1000,"flaky_window_seconds":120,"failure_threshold":2,"flaky_opens_incident":false}'
```
**Expect:** a `flaky` incident with severity one level lower (quotes medium → SEV4) and an `incident.opened` webhook with status
DEGRADED. Then a separate `service_down` incident: **two unresolved incidents for one service**. Each resolves on its own rule.
With `flaky_opens_incident` false (the default) no flaky incident is ever opened. *Automated:* `test_flaky_*` in `test_remaining_features.py`.

### 7-day availability (PO R9 optional)
```bash
pc "$P/api/services/$(sid payments)/metrics?window_hours=168" | python -m json.tool | head -12
```
**Expect:** `window_hours: 168`, with availability and budget over 7 days (only data that exists, never invented). Default without the parameter: 24 h.
The dashboard rows also carry `reliability_7d`. *Automated:* `test_seven_day_window`.

### Only one scheduler (no double checks)
```bash
curl -s $P/healthz                                        # "scheduler": "active"
# start a second Pulse against the same database; it must NOT check services (verified 2026-11-18):
docker compose run -d --rm --name pulse-standby -p 8001:8000 pulse
sleep 15; curl -s localhost:8001/healthz        # "scheduler": "standby"  (logs: "running as API-only standby")
docker stop pulse-standby
```
If the active instance dies, its lock is released and the next instance to start takes over. *Automated:* `test_only_one_instance_can_hold_the_scheduler_lock`, `test_second_app_instance_runs_as_standby`.

### AI transcripts (submission requirement)
```bash
bash scripts/export-transcripts.sh          # copies every Claude session of this project and its worktrees into transcripts/
opencode session list                       # shows each session id
opencode session export <session-id> > transcripts/opencode__<session-id>.json
```
No transcripts means zero for the AI-usage part of the assessment — re-run right before submitting.

## 4. Demo with the kit's drill

```bash
curl -s -X DELETE localhost:9900/api/received
bash pulse-starter-kit/scripts/incident-drill.sh --fast        # ~3 min; --reset afterwards if interrupted
```

| Drill step | Expected Pulse behaviour (default settings, observed 2026-11-18) |
|---|---|
| claims down 40 s | DEGRADED → DOWN on the 2nd failed check (~15 s) → incident + `incident.opened` |
| claims reset | RECOVERING 1/3, 2/3 → HEALTHY → incident resolved + `incident.resolved` |
| payments slow 3 s | DEGRADED `slow_response`, **no incident** (the reason for the 5 s timeout, D21) |
| quotes flaky 50 % | random: DEGRADED on single failures; an incident if 2 fail in a row; `flaky` if 3 failures land in the window |

Then show `pc $P/api/metrics/summary` (MTTD/MTTR), `curl localhost:9900/api/received`, and `curl $P/api/public/history`.

---

## 5. Verification log — what was actually run and seen (2026-11-18)

Everything below was executed against the final code. Repeat any line to reproduce.

| Check | Command | Result |
|---|---|---|
| Unit + integration | `.venv/Scripts/python -m pytest` | **121 passed** (1 warning: anyio deprecation, upstream) |
| Acceptance (10 scenarios) | `.venv/Scripts/python -m pytest -m acceptance` | **7 passed** in 143 s |
| Fresh start | `docker compose up -d --build` | `/healthz` → `{"status":"ok","db":"ok","services":4,"scheduler":"active"}` |
| Restart persistence | `docker compose restart pulse` | same incident still open, counters kept, no re-seed (also acceptance test 9) |

**Drill timeline observed live** (`incident-drill.sh --fast`, claims interval 15 s / high tier,
clock = local IST):

| Time | Event | Pulse behaviour |
|---|---|---|
| 20:21:11 | `claims -> down` | — |
| 20:21:29 | 1st failed check | DEGRADED, `http_error`, no incident |
| 20:21:42 | 2nd failed check | **DOWN + INC opened + `incident.opened` webhook** (MTTD ≈ 1 interval) |
| 20:21:52 | `claims -> reset` | — |
| 20:21:55 | 1st success | RECOVERING 1/3 (incident stays open) |
| 20:22:10 | 2nd success | RECOVERING 2/3 |
| 20:22:25 | 3rd success | HEALTHY → **incident RESOLVED + `incident.resolved` webhook** |
| 20:22:34–20:22:47 | `payments -> slow 3000` then reset | DEGRADED `slow_response`, **no incident, no webhook** |
| 20:23:00–20:23:26 | `quotes -> flaky 0.5` then reset | intermittent DEGRADED, no incident (default settings) |
| 20:24:43 | — | all four services Operational on `/api/public/status` |

Webhook receiver total for the drill: exactly **2** (one opened + one resolved for claims' single incident),
and `10.12.4.7` / `claims-db` appear in **neither** body.

**Known behaviours that look odd but are correct:**
- After `control/reset`, a previously flaky service can show DEGRADED (`flaky`) for up to one
  flaky window (~90–120 s): the stored failures are still inside the window. It clears by itself.
- Availability can read < 100 % right after a drill: the failed checks are real history.
- Under 10 checks in the window, availability reports `sufficient: false` ("insufficient data"),
  never a made-up 100 % (D32).

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Tests are skipped with "PostgreSQL not reachable on :5433" | `docker compose up -d db` |
| Acceptance tests are skipped | the full stack isn't up: `docker compose up -d --build` |
| Port 9900 is in use | `WEBHOOK_PORT=9950 docker compose up -d` and `RECEIVER_URL=http://127.0.0.1:9950 pytest -m acceptance` |
| A different Compose project name (folder name) | `COMPOSE_PROJECT=<name> pytest -m acceptance` (used by the restart test) |
| Slow tests on Windows | use `127.0.0.1` instead of `localhost` (IPv6 fallback, D40) |
| Two pytest runs at once | safe since D55: each session uses its own throwaway database |
| Leftover `pulse_test_<uid>` databases | dropped automatically at session end; a killed run leaves one behind — `docker exec ackoassignment-db-1 psql -U pulse -d pulse -c "DROP DATABASE IF EXISTS <name> WITH (FORCE);"` |
| Services left in a broken mode | `bash pulse-starter-kit/scripts/incident-drill.sh --reset` |
