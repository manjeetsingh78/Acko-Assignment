# Self-review

> Written with my AI assistants (all sessions are in `transcripts/`), then edited into my own words.
> Every claim below is something that actually happened in the build and is visible in the code, tests or logs.

## What works

All 10 must-haves, in the backend/API form the product owner asked for (D47), verified three ways:

- **121 unit + integration tests** and **7 acceptance tests** that run the brief's 10 scenarios
  against the live Compose stack (real kit services, real webhook receiver). All green.
- A manual run of `incident-drill.sh --fast`, observed with a clock: claims went
  DEGRADED on the first failed check (20:21:29), **DOWN + incident + `incident.opened` webhook on
  the second** (20:21:42, ~1 interval to detect), RECOVERING 1/3 → 2/3 after reset, then
  **HEALTHY + resolved + `incident.resolved` webhook on the third success** (20:22:34).
  Payments `slow 3000` showed DEGRADED with **no** incident — the exact reason I kept the 5 s
  timeout (D21) instead of the PO's proposed 3 s.
- Restart tests (unit and live): state, counters, open incidents and pending webhooks all
  survive `docker compose restart pulse`, because everything lives in Postgres, not memory.

## What doesn't (and why)

- **No UI** — PO decision (D47). I built a React dashboard first, then the PO said backend-only;
  the UI was archived outside the repo and the dashboard/status pages are JSON APIs with Swagger
  (`/docs`) as the console. Some time was spent on the detour, but it forced the API-first design
  I demo.
- **Flaky incidents are built but off by default** (D49): the PO hasn't answered Q3 yet, so a
  single per-service switch (`flaky_opens_incident`) turns them on without a redesign.
- **One shared operator account** — enough for the demo; per-user accounts need a users table.
- **No manual incidents / manual resolve** — no PO request; the answer to Q4 decides the second
  trigger type.
- **Load-balanced multi-instance scheduling** — standbys serve the API only (advisory lock, D50).

## What the AI got wrong (and how it was caught)

- **Flaky detection counted real-outage failures**, so a just-recovered service would have shown
  "DEGRADED (flaky)" for a whole window. Caught while writing the check processor; fixed by
  excluding failures recorded while DOWN, pinned by a test (D41).
- **Webhooks were scheduled with the database clock but checked against the app clock** — retries
  silently never fired when the clocks disagreed. A test caught it; fixed (D42).
- **The advisory-lock connection was pooled**: closing it returned the session to the pool with
  the lock still held, so a second instance could have started a second scheduler. A takeover
  test caught it; fixed by detaching the connection (D50).
- **`init_db` ran `ALTER TABLE` on every start** — `IF NOT EXISTS` still takes an
  AccessExclusive lock when it skips, which deadlocked check processing under concurrent access
  during the final verification session. Fixed by probing `information_schema` first (D54).
- **All tests shared one database**, so two overlapping pytest runs raced
  `drop_all`/`create_all` (pg_type clashes, deadlocks). Hit live during verification; fixed by
  giving each session its own throwaway database (D55).
- **It ran the test suite through `localhost` on Windows**, which tried IPv6 first: 214 s → 4 s
  after `127.0.0.1` (D40).
- **It tried to rewrite PLAN.md**, which the submission rules freeze at Gate 1. Caught by reading
  the brief; the revised plan lives in DESIGN.md instead (D37/D48).
- **The first transcript-export script copied 0 files** — `$HOME` resolves differently in Git
  Bash vs Windows, so it looked in the wrong directory. Caught because I ran it and read the
  output ("Copied 0") instead of trusting it.

## What I'd do next

- Get the PO's answers to Q1–Q4 (timeout, tiers, flaky incidents, second trigger) and flip the
  pending switches — the code is already ready for both answers.
- Manual incident creation and manual resolve.
- Per-user operator accounts with roles.
- Alembic migrations once the schema stops being a one-day schema.
- The AI-transcript fix is a good lesson for me: verify tool output (the "Copied 0" line) the
  first time, not the last.
