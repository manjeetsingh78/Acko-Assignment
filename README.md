# Pulse – service health monitoring and incident management (backend)

Pulse is a **backend service**. It checks the health of registered services on a schedule, decides whether each is
healthy, degraded, down or recovering, opens and resolves incidents automatically, notifies a webhook, and exposes
everything through a REST API:
- an operator API (login required) with exact technical detail;
- a public, customer-safe API for a status page.

The product owner asked for the backend only (D47). The interactive API docs at `/docs` act as the console.

Built for the ACKO DevOps Intern Product Buildathon on the supplied starter kit, which is **unchanged**.

| Document | What it contains |
|---|---|
| [DESIGN.md](DESIGN.md) | Current plan: PO answers → implementation, research, scope, open questions |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design: components, flows, state machine, data model, API |
| [TESTING.md](TESTING.md) | **How to test every requirement**: automated suites and copy-paste manual recipes |
| [PROGRESS.md](PROGRESS.md) | Progress log: what works, changes from the plan, bugs found and fixed |
| [REVIEW.md](REVIEW.md) | Self-review: what works, what doesn't, what the AI got wrong |
| [DECISIONS.md](DECISIONS.md) | Timestamped decision log (D1–D53) |
| [PLAN.md](PLAN.md) | Gate 1 plan, unchanged as the rules require |

## Run it (one command)

Requirements: Docker Desktop (or Docker Engine) with Compose v2.

```bash
docker compose up --build
```

| What | URL |
|---|---|
| Pulse API + interactive docs | http://localhost:8000 → `/docs` |
| Public status (JSON, no login) | http://localhost:8000/api/public/status |
| Pulse health | http://localhost:8000/healthz |
| Webhook receiver (kit) | http://localhost:9900 |
| Mock services (kit) | http://localhost:7001 … 7004 |
| PostgreSQL | `localhost:5433`, user/password/db `pulse` |

Operator login: `POST /api/auth/login` with `{"username":"operator","password":"pulse-demo"}` (demo-only; override with
`PULSE_OPERATOR_USERNAME`, `PULSE_OPERATOR_PASSWORD`, `PULSE_SESSION_SECRET`). The session cookie authorises the operator
endpoints, including from the Swagger page.

## Try it in 3 minutes (live demo)

With the stack running, paste these commands. On Windows use `curl.exe`; add `--noproxy "*"` if a system proxy
intercepts `localhost`.

```bash
# 0) log in once and reuse the session cookie
curl -c cookies.txt -X POST localhost:8000/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username":"operator","password":"pulse-demo"}'

# 1) see the dashboard: 4 services, all HEALTHY
curl -b cookies.txt localhost:8000/api/dashboard/summary

# 2) break claims (the kit's control endpoint) and watch Pulse notice
curl -X POST localhost:7003/control/down
#    check 1 → DEGRADED, check 2 → DOWN + incident + webhook. ~30 s later look:
curl -b cookies.txt localhost:8000/api/incidents?status=active
curl -s localhost:9900/api/received          # the incident.opened webhook is here

# 3) customers never see internal detail — only fixed wording:
curl -s localhost:8000/api/public/status     # "Payments / Claims: Service disruption" etc.

# 4) acknowledge and add a note as the on-call operator
curl -b cookies.txt -X POST localhost:8000/api/incidents/1/acknowledge
curl -b cookies.txt -X POST localhost:8000/api/incidents/1/notes \
     -H "Content-Type: application/json" -d '{"note":"DB failover in progress"}'
curl -b cookies.txt localhost:8000/api/incidents/1   # full timeline

# 5) recover: 3 good checks resolve the incident and send incident.resolved
curl -X POST localhost:7003/control/reset
#    ~45 s later:
curl -s localhost:9900/api/received          # resolved webhook arrived
curl -b cookies.txt localhost:8000/api/metrics/summary   # MTTD / MTTA / MTTR

# 6) make payments slow (3 s < 5 s timeout → DEGRADED, no incident, no webhook)
curl -X POST "localhost:7002/control/slow?ms=3000"
curl -b cookies.txt localhost:8000/api/dashboard/summary  # payments DEGRADED, 0 incidents
curl -X POST localhost:7002/control/reset
```

Or run the whole thing scripted: `bash pulse-starter-kit/scripts/incident-drill.sh --fast` (~3 min).

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 9900 already in use | `WEBHOOK_PORT=9950 docker compose up --build` (then webhooks still go to the container name, only the receiver's *host* port changes) |
| Port 5433 / 8000 in use | stop the other app, or change the left side of the `ports:` mapping in `docker-compose.yml` |
| `localhost` works but tests can't reach the DB | the tests connect to `127.0.0.1:5433`; run `docker compose up -d db` first |
| A system proxy intercepts `localhost` (common on Windows with `Invoke-RestMethod`) | use `curl.exe --noproxy "*"` |
| Something looks stuck from a previous demo | `bash pulse-starter-kit/scripts/incident-drill.sh --reset`, then wait one check interval |

Pulse seeds the 4 kit services on first start and starts checking immediately. Data lives in the `pgdata` volume and
survives restarts; `docker compose down -v` wipes it. If port 9900 is taken: `WEBHOOK_PORT=9950 docker compose up --build`.

## API at a glance

| Purpose | Endpoint |
|---|---|
| Catalogue | `GET/POST /api/services`, `GET/PATCH /api/services/{id}` |
| Check history, reliability | `GET /api/services/{id}/checks`, `GET /api/services/{id}/metrics` |
| Incidents | `GET /api/incidents`, `GET /api/incidents/{id}`, `POST …/acknowledge`, `POST …/notes` |
| Dashboard data | `GET /api/dashboard/summary` |
| Product metrics | `GET /api/metrics/summary` |
| Public status page data | `GET /api/public/status`, `GET /api/public/history` |
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |

## Tests

```bash
docker compose up -d db                                  # tests use the real Postgres (:5433); each pytest session
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # creates its own throwaway test database
.venv/Scripts/python -m pytest                           # 121 unit + integration tests (~110 s)
docker compose up -d --build
.venv/Scripts/python -m pytest -m acceptance             # 7 tests = the brief's 10 scenarios against the live kit (~2–3 min)
```

See **[TESTING.md](TESTING.md)** for what each suite covers and a manual `curl` recipe for every requirement.

## Demo

```bash
bash pulse-starter-kit/scripts/incident-drill.sh --fast     # ~3 min
```
- **claims down:** DOWN + incident + `incident.opened` webhook after the 2nd failed check. After 3 good checks: resolved + `incident.resolved`.
- **payments slow (3 s):** DEGRADED, no incident.
- **quotes flaky:** a mix of DEGRADED, `flaky` and possibly an incident.

Details in TESTING.md §4.

## What works

All 10 must-haves, in backend form:
1. Catalogue API with validation.
2. Tiered independent scheduled checks, with every result stored.
3. HEALTHY / DEGRADED / DOWN / RECOVERING, plus flaky detection.
4. Automatic incidents with acknowledgement, notes and a timeline.
5. 24 h availability against the target, plus the error budget.
6. Dashboard API.
7. Public status API.
8. Webhooks with retries and delivery history.
9. Product metrics.
10. One-command run.

Also implemented:
- operator login;
- 7-day availability (`?window_hours=168`);
- an optional per-service setting to open incidents for flaky services (`flaky_opens_incident`, off by default per the PO);
- a single-scheduler guarantee (Postgres advisory lock; `/healthz` shows `active` or `standby`);
- `scripts/export-transcripts.sh`.

## What doesn't (yet)

- **No UI, by PO decision.** A React UI was built and then archived outside the repository.
- Flaky incidents are switched off by default until the PO answers (one setting per service). No manual incidents or manual resolve.
- One shared operator account. Extra instances run as API-only standbys; there is no load-balanced scheduling.

## Configuration defaults

| Setting | Default |
|---|---|
| Interval by criticality | critical 10 s · high 15 s · medium 20 s · low 30 s (payments critical, claims high, quotes medium, notifications low) |
| Timeout / slow threshold | 5000 ms / 1000 ms (slow < timeout < interval is enforced) |
| Failures → DOWN / successes → recovered | 2 / 3 |
| Flaky | 3 failures in max(60 s, 6 × interval) |
| Reliability target | 99.9 % |

All settings can be changed per service via `PATCH /api/services/{id}`. Global defaults are environment variables (see `app/config.py`).
