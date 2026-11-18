# Pulse – Prompt Playbook

How to use this file:
- Send these prompts **one at a time, in order**. Wait for each step to finish and check it before sending the next one.
- Fill in every `<...>` placeholder with **your own** decision or answer. The graders check that you make the decisions, not the AI.
- Read every diff before you accept it. If something looks wrong, say so: "This is wrong because ...". Those corrections earn marks.
- Commit after each step. Log each decision in DECISIONS.md with the time.

---

## PHASE 1 – Discovery & Plan (0:15 – 1:15)

### P1. Understand the problem
```
Read the Pulse problem statement (the PDF in the parent folder) and starter-kit/README.md and starter-kit/mock-service/app.py.
In your own words, list: (1) the 3 user groups and the outcome each needs, (2) every must-have,
(3) anything in the starter kit that looks like a hidden test or trap. Don't write code yet.
```

### P2. Prepare product owner questions
```
Give me the 10 most important questions to ask the product owner: ones whose answers change what I build
(state rules, thresholds, SLO window, what the public page can show, notification content, auth).
For each one, say what I'd build differently depending on the answer.
```
→ *Ask the PO yourself. Copy the answers word for word.*

### P3. Research similar products
```
Compare how Atlassian Statuspage, Uptime Kuma, Better Stack and PagerDuty handle: health check rules,
avoiding flapping, incident lifecycle (open/ack/resolve), public status pages, and SLO/error budgets.
Give me 5–8 concrete ideas I should copy and 2–3 I should deliberately skip for a 6-hour build.
```

### P4. Draft PLAN.md
```
Here are the product owner's answers:
<paste Q&A>

My decisions:
- Stack: <e.g. Python FastAPI + SQLite + httpx + server-rendered HTML, pytest>
- Down rule: <e.g. 3 failed checks in a row, or timeout>
- Degraded rule: <e.g. response > 1000 ms, or failure ratio > X in last N checks>
- Recovery rule: <e.g. 2 successful checks in a row>
- Incident opens on: <down only / down + degraded>
- SLO window: <e.g. rolling 30 days, plus a 1-hour view for the demo>; degraded counts as <up/down>
- Out of scope: <list, with reasons>

Write PLAN.md (short, bullet points) covering: problem in my words, PO Q&A, research findings, scope
(must-haves first, what's left out and why), ASCII system diagram, data model (tables + key columns),
main screens (ASCII wireframes), test plan (unit / integration / acceptance), and metrics.
Don't decide anything I haven't decided. List open questions instead.
```
→ *Read it critically. Fix anything that isn't your view. Commit: "Gate 1: PLAN.md".*

### P5. Start the decision log
```
Create DECISIONS.md with a table (time, decision, reason, alternatives considered).
Add the decisions from PLAN.md. Times: <current time>.
```

---

## PHASE 2 – Build (1:15 – 3:30)

### P6. Skeleton + one-command run
```
Set up the project skeleton per PLAN.md: app folder, config, SQLite schema/migrations, a /healthz endpoint,
a Dockerfile, and a root docker-compose.yml that includes the starter kit (unchanged) plus Pulse, so
`docker compose up --build` starts everything. Inside the network, Pulse must call services by name (http://claims:8080).
Seed the catalogue with the 4 mock services. Explain each file in 1 line. Then run it and show me it starts.
```

### P7. Service catalogue (must-have 1)
```
Build the service catalogue: CRUD API (name, health_url, interval_seconds, timeout_ms, owner, slo_target)
with validation (interval >= <X>s, target between 0 and 100, unique name), plus a UI page to list/add/edit.
Write unit tests for validation and integration tests for the API. Run the tests and show the results.
```

### P8. Health checker (must-have 2)
```
Build the scheduler: one async task per service, each on its own interval, each request with its own timeout,
so a 3 s slow service never delays others. Store every check (service, time, ok, status_code, latency_ms, error).
When a service is added, edited or removed, its task must start, restart or stop.
Prove isolation with a test: one service set to slow, the others still checked on time.
Show me how you measured it.
```

### P9. State machine (must-have 3)
```
Implement the service state machine as a PURE function (no DB, no I/O) using my rules:
<paste your down/degraded/recovery rules>.
Write table-driven unit tests covering: healthy→degraded, degraded→down, down→healthy needs <N> successes,
flaky at 50% must NOT flap, a single blip must NOT open an incident. Explain any edge case you're unsure about
instead of guessing.
```

### P10. Incidents (must-have 4)
```
Build incidents: auto-open when a service goes <down>, auto-resolve on recovery. One open incident per
service at most. Statuses: open → acknowledged → resolved. Operators can acknowledge and add notes.
Every change writes an incident_events row (timeline). API + UI page with the timeline.
Tests: no duplicate incidents under flapping, ack after resolve is rejected, timeline order is correct.
```

→ *Gate 2 (3:30):*
```
Write PROGRESS.md: what works (verified how), what's next, and plan changes with reasons.
Be honest and don't claim anything we haven't tested.
```

---

## PHASE 3 – Build, test, metrics (3:30 – 5:30)

### P11. Notifications (must-have 8)
```
Send a webhook to the receiver on incident opened and resolved: exactly once each, with retry + backoff
if the receiver is down, and record each attempt in a notifications table.
Payload: <your fields: service, owner, severity, started_at, duration, link>.
Never include the raw internal error text in payloads that might go to customers. Test it against the real receiver
(GET /api/received).
```

### P12. Reliability / SLO (must-have 5)
```
Calculate per service: availability over <window> = <your formula>, the target, error budget total,
budget used, budget remaining (in minutes and %). Put it in a pure function with unit tests on hand-computed
examples (including no data, and a service added mid-window). Show it on the dashboard.
```

### P13. Operator dashboard (must-have 6)
```
Build the operator dashboard: status per service (colour + text), latest latency and a small recent-latency
chart, SLO/budget, open incidents on top, past incidents, and links to each incident timeline. Auto-refresh every
<N>s. Keep it simple and readable.
```

### P14. Public status page (must-have 7)
```
Build a read-only public /status page: current status per service in customer-friendly words and the
last <N> days of history. It must NOT show internal errors, IPs, hostnames, owners or operator notes.
Add a test that sets a service down and asserts "10.12.4.7" and "timeout" never appear in the page HTML.
```

### P15. Product metrics (must-have 9)
```
Add a metrics page (and /metrics JSON): MTTD (failure start → incident open), MTTA, MTTR, incident count,
check success rate, check latency p50/p95, scheduler lag (how late checks run vs schedule), webhook
delivery success. Explain how each is computed and where its timestamps come from.
```

### P16. Acceptance test with the drill
```
Write an acceptance test/script that starts the stack, runs `incident-drill.sh --fast`, and then checks:
claims got exactly 1 incident opened+resolved, payments shows degraded (per my rules), quotes flaky
behaviour matches my rule, webhooks received = expected, public page leaks nothing. Run it and show me the output.
```

### P17. Review your own code
```
Review the whole codebase as a strict senior engineer: bugs, race conditions in the scheduler, timezone
issues, missing tests, anything that breaks my PLAN rules. List the findings ranked by severity. Don't fix yet.
```
→ *You choose which ones to fix:* `Fix findings <numbers>. Skip <numbers> because <reason>.`

---

## PHASE 4 – Finish (5:30 – 6:00)

### P18. README
```
Write README.md: what Pulse is, one-command run from a fresh clone, URLs of every page, how to run unit /
integration / acceptance tests, how to run the demo drill, what works and what doesn't (honestly).
Then follow the README yourself from scratch and fix anything that fails.
```

### P19. REVIEW.md
```
Help me draft REVIEW.md (half a page): what works, what doesn't, what the AI got wrong today (look back
through this session for real examples), and what I'd do next. I'll edit it in my own words.
```

### P20. Demo prep
```
Give me a 5-minute demo script using the drill, and 10 likely questions the Acko engineers will ask about my
code and decisions, with where in the code the answer lives. Also suggest 3 small "live change"
requests they might give me (e.g. change the down threshold) and which file/line I'd edit for each.
```

### Final checklist (do it yourself)
- [ ] `/export` this session → copy into `transcripts/` (also copy from `~/.claude/projects/`)
- [ ] Export any other AI chats you used → `transcripts/`
- [ ] PLAN.md unchanged since Gate 1; later changes go in DECISIONS.md
- [ ] All pushed **before 6:00**

---

## Tips that earn marks
- **Give context:** paste the PO answers and your rules, and point to the files.
- **Make decisions yourself:** "I chose X because Y. Implement X."
- **Check the AI's work:** "Run the tests." "Show me it works." "Why did you do it this way?"
- **Push back when it's wrong:** these moments show up in the transcript.
- **Keep it small:** a smaller product that works and is tested beats a big broken one.
