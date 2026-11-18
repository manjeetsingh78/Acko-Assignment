# Pulse – Decision Log

Each decision is timestamped (local time, 2026-11-18). PLAN.md stays as it was at Gate 1, and later changes are recorded here.
**Source:** "Me" = my own decision. "PO" = the product owner's answer. "Me (PO not yet asked)" = my working decision until the PO answers.

| # | Time | Decision | Reason | Alternatives considered | Source |
|---|---|---|---|---|---|
| D1 | 17:58 | Stack: Python FastAPI + SQLite + httpx (async) + Jinja2 server-rendered HTML + pytest | One language, async fits per-service scheduling, no frontend build step, fast tests | Node/Express; React SPA; PostgreSQL | Me |
| D2 | 17:58 | SQLite in WAL mode, file on a Docker volume | Meets "history survives restarts" with no extra container; WAL lets concurrent check writes avoid blocking | PostgreSQL (closer to production, more setup and slower test fixtures) | Me |
| D3 | 17:58 | Per-service check interval, default 10 s, allowed 6–60 s | Quick detection without excessive load; min > timeout so checks never overlap | Default 30 s (too slow for the 40 s `--fast` drill); 5–60 s (conflicts with the 5 s timeout) | Me (PO not yet asked) |
| D4 | 17:58 | Request timeout 5 s; must be < interval (validated) | Drill `slow 3000ms` returns 200 after ~3 s. A 3 s timeout would wrongly show payments as Down instead of Degraded | 3 s timeout; 4 s timeout (less margin) | Me (PO not yet asked) |
| D5 | 17:58 | Healthy = 2xx ≤ 1000 ms; slow = 2xx > 1000 ms | Separates "responding slowly" from "unavailable" | Latency percentile over a window (more complex) | Me (PO not yet asked) |
| D6 | 17:58 | Failure = non-2xx, connection error or timeout. 1st failure → DEGRADED; **2 consecutive failures → DOWN** | Filters one-off blips; 2 checks at 10 s detects claims within the 40 s `--fast` window | 3 failures (earlier draft; too slow for the drill); 1 failure (false positives) | Me (PO not yet asked) |
| D7 | 17:58 | **2 consecutive successes** → HEALTHY and the incident resolves; any failure resets the count | One success doesn't prove recovery (Better Stack recovery period) | 1 success; time-based recovery period | Me (PO not yet asked) |
| D8 | 17:58 | Incidents open on **DOWN only**; Degraded is shown but opens no incident and sends no webhook | Degraded is a warning, not an outage; avoids paging on slowness | Down + Degraded (earlier draft) | Me (PO not yet asked) |
| D9 | 17:58 | At most **one unresolved incident per service**; further failures append to its timeline | Prevents duplicate incidents and notifications (PagerDuty dedup) | Allow several, merge later | Me (PO not yet asked) |
| D10 | 17:58 | Incident lifecycle Open → Acknowledged → Resolved; ack or notes on a resolved incident are rejected | Industry standard (PagerDuty, Better Stack); a strict state machine keeps the timeline trustworthy | Statuspage's 4 public stages (need a human writing updates) | Me |
| D11 | 17:58 | Flaky services: accepted that each separate outage opens its own incident; the dashboard shows failure % | Keeps the rules simple and consistent; flakiness is visible | Reopen window; higher thresholds (slower claims detection) | Me |
| D12 | 17:58 | Webhooks on `incident.opened` and `incident.resolved` only; JSON POST; no internal error text in the payload; owner included | Must-have #8; the receiver accepts any path and JSON and always returns 200 (verified in `webhook-receiver/app.py`) | Notify on ack, degraded, notes (noisy) | Me |
| D13 | 17:58 | Public page shows only status in fixed customer wording, start time, a friendly description, and recent history. No errors, IPs, hosts, owners, notes or latency | Kit's `INTERNAL_ERROR` contains `10.12.4.7` and a DB host; rendering only fixed strings means nothing internal can leak | Filtering error text (fragile) | Me (PO not yet asked) |
| D14 | 17:58 | Availability over a **rolling 24 h**, check-based: successful ÷ eligible checks. No data ≠ healthy. 7 days if time allows | Meaningful within a demo; does not invent data for before monitoring began | Rolling 30 days + 1 h view (earlier draft; mostly empty during the demo) | Me (PO not yet asked) |
| D15 | 17:58 | Error budget = window minutes × (1 − target), e.g. 1.44 min for 99.9% / 24 h; shown in minutes and %; can go negative ("exhausted") | Minutes are easier to read than percentages | Percentage only | Me |
| D16 | 17:58 | Authentication deferred for the MVP; documented as required in production | Not a must-have; listed under "going further" | Basic auth on operator routes | Me (PO not yet asked) |
| D17 | 17:58 | Priorities: P0 = catalogue, checks, states, auto incidents + ack, public page, webhooks, one-command run. P1 = timeline + notes, reliability, dashboard, metrics. P2 (cut) = auth, advanced analytics, subscriptions | Get the full detect → incident → notify → recover loop working first. All must-haves are still built, P1 second | Build features in the problem statement's numbering order | Me (PO not yet asked) |
| D18 | 17:58 | Out of scope: auth, customer subscriptions, advanced analytics | P2; not required for a complete product | — | Me |

## Open (to decide and log here)

| # | Question |
|---|---|
| O-1 | Does a slow 2xx count as a success for the 2-successes recovery rule? If so, does the service recover to DEGRADED? |
| O-2 | After the 1st success following DOWN: show "DOWN (recovering)" or DEGRADED? |
| O-3 | Does a slow 2xx count as successful in the availability formula? |
| O-4 | Error budget used: failed checks × interval, or time spent in DOWN? |
| O-5 | An interval changed mid-window: how are eligible checks counted? |
| O-6 | Webhook path (proposed `/pulse`) and retry policy (proposed 3 attempts, 1/2/4 s backoff) |
| O-7 | Confirm or reject the out-of-scope candidates: escalation/on-call, maintenance windows, non-HTTP/multi-region checks |
| O-8 | Dashboard auto-refresh interval (proposed 5 s) |

---

## Product owner answers (received after Gate 1, logged 18:25)

PLAN.md stays unchanged (submission rule). The working design that follows these answers is **DESIGN.md**.
O-1 to O-6 above are resolved by D23–D32 below.

| PO topic | PO answer (summary of the PO's words) | Effect |
|---|---|---|
| Check frequency | Tier-based by criticality; proposed critical 10 s, high 30 s, medium 60 s, low 120 s; all configurable; don't hardcode names | supersedes D3 → D20 |
| Timeout / latency | "Research and recommend"; proposed timeout 3 s, slow 1 s, 2xx = success | D21, D22 |
| Failures | 2 consecutive failures → DOWN + incident; 1st → DEGRADED; a success resets | confirms D6 |
| Recovery | **3** consecutive successes → HEALTHY + resolve; 1st and 2nd = RECOVERING | supersedes D7 → D23 |
| Flaky | X failures in a rolling Y s → DEGRADED (reason recorded), configurable; incident or not = unresolved, so DEGRADED only for now | D26 (replaces D11) |
| Multiple incidents | Allowed for the same service while earlier ones are unresolved; avoid duplicates per trigger | supersedes D9 → D27, D28 |
| Webhooks | "Use your design skills": opened + resolved, delivery status, bounded retry, no duplicates | D29 |
| Audiences | Customer-friendly public page; exact technical detail for engineers; separate schemas and permissions | D30 (extends D13) |
| Reliability | Rolling 24 h (7 days optional), per-service target, error budget, no invented data | D32 |
| Priorities | Must-haves from the problem statement first | confirms D17 |
| Persistence | Must survive restarts; **use PostgreSQL** | supersedes D2 → D19 |
| Auth | "Implement if possible"; basic operator login | supersedes D16 → D31 |

## Decisions after the PO answers

| # | Time | Decision | Reason | Alternatives considered | Source |
|---|---|---|---|---|---|
| D19 | 18:25 | **PostgreSQL 16** in Compose (named volume), replacing SQLite. **Supersedes D2.** Host port **5433** | The PO asked for Postgres. PostgreSQL 18 already runs on this laptop's 5432 | SQLite (D2) | PO |
| D20 | 18:25 | Criticality tiers drive the default interval: critical 10 s, high 15 s, medium 20 s, low 30 s. Every value can be overridden per service. **Supersedes D3** | The PO's 30/60/120 s can't satisfy the drill: claims is down only 40 s in `--fast` (1–2 checks at 30 s, so maybe no incident); quotes at 60 s can never reach 3 failures in 60 s | PO's 10/30/60/120 (kept as the documented production profile) | Me (to confirm with PO, Q2) |
| D21 | 18:25 | Request timeout stays **5 s** (PO proposed 3 s). Validated `slow < timeout < interval` | The drill's `slow ms=3000` would hit a 3 s timeout, so payments would show DOWN, open an incident and page someone, though the drill calls it slow (contradicts "don't confuse latency with failure") | 3 s (PO proposal); 4 s | Me (to confirm with PO, Q1) |
| D22 | 18:25 | Slow threshold 1000 ms; outcomes OK_FAST / OK_SLOW / FAIL_HTTP / FAIL_TIMEOUT / FAIL_CONNECTION | Matches the PO proposal and D5; five outcomes give the dashboard exact reasons | – | PO + Me |
| D23 | 18:25 | Recovery needs **3** consecutive successes; new state **RECOVERING** shown in between. **Supersedes D7** | PO answer | 2 successes (D7) | PO |
| D24 | 18:25 | A failure while RECOVERING returns straight to **DOWN** (same incident, `recovery_interrupted` event), not to DEGRADED | Going via DEGRADED would "enter DOWN" again and open a duplicate incident for the same outage. Better Stack also resets the recovery period on any failure | DEGRADED then DOWN (the brief's "DOWN or DEGRADED" wording) | Me |
| D25 | 18:25 | A slow 2xx counts as a **success** for recovery and availability; after recovery the service shows DEGRADED if the last check was slow. Resolves O-1, O-2 (RECOVERING state), O-3 | Customers can transact; slowness is shown as degradation, not as downtime | Slow = not recovered (could keep incidents open for ever under load) | Me |
| D26 | 18:25 | Flaky = `flaky_failure_count` failed checks within `flaky_window_seconds` (counted from `health_checks`, so it survives restart). Default 3 failures, window = max(60 s, 2 × 3 × interval). Only decides HEALTHY vs DEGRADED, never overrides DOWN/RECOVERING. Reason `flaky` stored. **No incident** until the PO answers Q3. Config rejected if window < count × interval. Replaces D11 | The PO asked for it; a window that can't hold X checks makes the rule impossible to trigger | Failure % over the last N checks (simpler, but not what the PO asked for) | PO + Me |
| D27 | 18:25 | Several unresolved incidents per service **allowed** (the partial unique index is removed). Incidents are **edge-triggered** on entering DOWN; `trigger_key = down:{service_id}:{first_failed_check_id}` is UNIQUE, which prevents duplicates. **Supersedes D9** | PO answer; the unique key makes dedup a DB guarantee, not just app logic | One open per service (D9) | PO + Me |
| D28 | 18:25 | Confirmed recovery resolves only unresolved incidents of that service with `trigger_type = service_down`; other triggers are untouched | "Recovery" is exactly the clear condition for a down-trigger; it doesn't silently close unrelated incidents | Resolve all open incidents of the service | Me |
| D29 | 18:25 | Webhooks through an **outbox**: a `notifications` row is written in the same transaction as the incident change; a background sender POSTs to `http://webhook-receiver:9000/pulse`. Max 4 attempts (5/15/45 s backoff on timeout/5xx/429); other 4xx fail at once. `UNIQUE(incident_id, event_type)`. At-least-once delivery with `event_id`. Payload carries an error category, never raw error text. Resolves O-6 | Webhook failure can't block checking; pending sends survive restart; no duplicates | Send inline in the checker (blocks it); fire-and-forget (no delivery history) | Me |
| D30 | 18:25 | Separate Pydantic schemas for public vs operator responses. Public text comes only from fixed templates and `is_public` timeline events; operator notes and `error_message` are never public | Makes the kit's `10.12.4.7` leak impossible by construction; tested | Filtering error strings (fragile) | Me |
| D31 | 18:25 | Auth: one operator account from env vars (demo values in compose, marked demo-only), signed HttpOnly session cookie, `require_operator` on operator pages and APIs. **Supersedes D16** | PO "if possible"; smallest thing that is real auth | JWT; a users table; HTTP Basic | PO + Me |
| D32 | 18:25 | Availability = successful ÷ total checks over a rolling 24 h; shown with check count and covered time; < 10 checks → "insufficient data". Downtime ≈ failed fraction × covered minutes; budget = covered minutes × (1 − target) − downtime. Resolves O-4; O-5 (interval change mid-window) documented as a sampling limitation | Matches the PO formula; honest about gaps (Pulse not running = missing, not healthy) | Time-in-DOWN from state changes | PO + Me |
| D33 | 18:25 | Severity derived from criticality: critical → SEV1 … low → SEV4 | One config knob, no extra input | Manual severity | Me |
| D34 | 18:25 | `UNKNOWN` state until the first check ("No data yet" publicly) | Distinguishes missing data from healthy | Default HEALTHY (lies) | Me |
| D35 | 18:25 | Scheduler: a supervisor (every 5 s) keeps one sequential asyncio loop per enabled service; httpx with a per-service timeout; semaphore(10); DB writes via `asyncio.to_thread`; config re-read every cycle; no catch-up bursts. Single instance (optional Postgres advisory lock) | A sequential loop makes overlap for one service impossible; independent tasks isolate slow services | APScheduler (another library to explain); one global tick | Me |
| D36 | 18:25 | SQLAlchemy 2.0 **sync** + psycopg 3; `create_all` at startup, no Alembic | Easier to read and explain than async ORM; migrations are unnecessary for a 1-day schema | Async SQLAlchemy + asyncpg; Alembic | Me |
| D37 | 18:25 | PLAN.md kept unchanged; revised design lives in DESIGN.md; changes logged here | Submission rule: "Your Gate 1 plan, unchanged" | Rewrite PLAN.md | Rules |
| D38 | 18:25 | ⏳ **Frontend:** server-rendered Jinja (D1) vs React + Vite + TS + Tailwind | Awaiting my decision | – | Me (pending) |
| D39 | 18:30 | Skeleton rebuilt on PostgreSQL (SQLite migration runner removed, SQLAlchemy models + `create_all`). The old test asserting "one open incident per service" was replaced by tests asserting the opposite (D27) | Applies D19/D27/D36 before any feature is built on the wrong DB | Keep SQLite until later (rework grows with every feature) | Me |
| D38b | 18:40 | **Frontend: server-rendered Jinja** (resolves D38): pages in `app/templates`, inline SVG charts, `<meta refresh>` for live pages | No second build/container; same-origin cookie auth; time goes to the core rules. No answer from me yet, so the AI went with its recommendation; I can still change it | React + Vite + TS + Tailwind | AI recommendation (to confirm) |
| D41 | 18:45 | Flaky detection ignores failures recorded while the service was DOWN | The PO defines flaky as failing repeatedly "but not continuously down"; otherwise a recovered service looks flaky for a whole window | Count all failures | Me |
| D42 | 18:50 | Outbox rows get `next_attempt_at` from the app clock, not Postgres `now()` | The notifier compares against the app clock; mixing the two delayed webhooks when the clocks differed (caught by a test) | DB clock everywhere | Me |
| D43 | 18:55 | Acceptance tests run against the live stack and speed it up by PATCHing services to 2 s intervals, then restoring the config | Tests the real kit and real webhooks in ~2 min; also exercises live config changes | Wait for the default 10–30 s intervals (~15 min) | Me |
| D44 | 19:05 | **Frontend = React 19 + Vite + TypeScript + Tailwind + Recharts** (supersedes D1's Jinja and D38b), served by FastAPI from the same origin; the Jinja pages were removed | Asked to "complete frontend and connect backend with frontend" (the stack in my brief). Same origin keeps cookie auth simple (no CORS, no tokens in JS) | Keep Jinja; separate frontend container with CORS | Me (request) |
| D45 | 19:15 | Docker builds the frontend in a Node 24 stage with `npm install` (not `npm ci`) | The lockfile made on Windows omits Linux-only optional packages (known npm issue), so `npm ci` fails in Linux; install still pins locked versions | Generate the lockfile in Linux; commit node_modules | Me |
| D46 | 19:20 | UI tested end-to-end with Playwright driving the installed Edge (`pytest -m e2e`); dev-only deps in `requirements-dev.txt` | Tests what a user actually does (login, ack, notes, config errors, public page leak check); no browser download needed | Component tests only (would not prove the backend connection) | Me |
| D47 | 19:35 | **Backend only** (PO): no UI in the product. Dashboard and status page are delivered as JSON APIs (`/api/dashboard/summary`, `/api/public/*`); `/` redirects to the Swagger UI. **Supersedes D44 and D46**; the React UI source was archived outside the repo (`.claude/archive/react-frontend`) | PO instruction. It also keeps the review focused on the monitoring and incident logic | Keep the React UI | PO |
| D48 | 19:35 | The current plan lives in DESIGN.md ("Backend Plan v2"), not PLAN.md | Submission rule: PLAN.md = the unchanged Gate 1 plan; later changes go in the decision log | Overwrite PLAN.md | Rules |
| D49 | 19:40 | **Flaky incidents as a per-service option `flaky_opens_incident`, OFF by default.** When on: crossing the flaky threshold opens a separate incident (trigger `flaky`, one severity level lower, own webhooks); it resolves when fewer than X failures remain in the window. Independent of `service_down` incidents, so both can be open at once (PO R6) | Keeps the PO's current rule (flaky = DEGRADED only) while making the answer to Q3 a single setting; it is also the "second trigger" that R6 allows | Wait for the PO; always open | Me (PO Q3 pending) |
| D50 | 19:40 | **Single scheduler guaranteed by a Postgres advisory lock**: only the instance holding it runs the scheduler and notifier; others serve the API as `standby` (`/healthz` shows `scheduler`). The lock connection is detached from the pool so closing it really releases the lock (bug caught by a test) | Prevents double checks and duplicate webhooks if two instances run; no extra infrastructure | Leader election service; documenting the limitation only | Me |
| D51 | 19:40 | 7-day availability: `GET /api/services/{id}/metrics?window_hours=168` (1–720 h), and `reliability_7d` on each dashboard row. Default stays 24 h | PO R9 "optional seven-day history if time permits" | A separate endpoint | Me |
| D52 | 19:40 | Small idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` list in `db.init_db`, for columns added after the first release | `create_all` never alters existing tables, and the live DB must keep its history | Alembic (more to explain for one column); drop the volume (loses history) | Me |
| D53 | 19:40 | `scripts/export-transcripts.sh` copies all project and worktree session files into `transcripts/` | Required by the brief; rerun just before submitting | Manual copy | Me |
| D40 | 18:30 | DB tests use a real Postgres: a separate `pulse_test` database in the Compose `db` container (created by `db/init/`), tables dropped/recreated per test. Connect via `127.0.0.1`, not `localhost` | Constraints behave as in production. On this Windows machine `localhost` tried IPv6 first: 214 s → 4 s for the suite | SQLite for tests (different SQL dialect); testcontainers (extra dependency) | Me |
| D54 | 21:15 | `init_db` runs the `ALTER TABLE` upgrade **only when the column is actually missing** (probe `information_schema` first, D52's list became `(table, column, sql)` tuples) | `IF NOT EXISTS` still takes an AccessExclusive lock on every start even when it skips, which deadlocked check processing when two engines touched the DB concurrently during verification | Run DDL only after a schema-version table (Alembic-style, more machinery) | Me |
| D55 | 21:15 | Every pytest session gets its **own throwaway database** (`pulse_test_<uid>`, created and dropped by `conftest.py`; `PULSE_TEST_DATABASE_URL` still overrides) | D40's single shared `pulse_test` made two overlapping pytest runs race `drop_all`/`create_all` (pg_type clashes, deadlocks, phantom missing tables) — hit live during verification | File-based locks; a run-queue (both slower and fragile) | Me |

## Still open

| # | Question |
|---|---|
| Q1 | PO: accept the 5 s timeout instead of 3 s? (D21) |
| Q2 | PO: accept the 10/15/20/30 s tiers? (D20) |
| Q3 | PO: does crossing the flaky threshold open an incident? (D26) |
| Q4 | PO: which second trigger type (flaky or manual) should create additional unresolved incidents? (D27) |
| O-7 | Out-of-scope candidates (unchanged) |
| O-8 | Dashboard auto-refresh interval (proposed 5 s) |
