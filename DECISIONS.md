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
