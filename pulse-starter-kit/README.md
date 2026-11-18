# Pulse starter kit

Everything you need to build **Pulse**, the status and incident hub. Read the problem statement first.

## Start the kit

```
docker compose up --build
```

> **Using Podman?** Run `podman compose up --build` instead (needs `podman-compose` or Docker Compose installed). Everything else is the same.

This starts four mock Acko services and a webhook receiver:

| Service | Address | Health check |
| --- | --- | --- |
| `quotes` | http://localhost:7001 | http://localhost:7001/health |
| `payments` | http://localhost:7002 | http://localhost:7002/health |
| `claims` | http://localhost:7003 | http://localhost:7003/health |
| `notifications` | http://localhost:7004 | http://localhost:7004/health |
| Webhook receiver | http://localhost:9900 | Stands in for chat, email and paging. Anything POSTed to it appears on its page. `GET /api/received` returns the list as JSON; `DELETE /api/received` clears it. |

From inside Docker (for example, if you run Pulse in the same Compose network), use the service names instead: `http://claims:8080/health`, `http://webhook-receiver:9000`.

> **Port already in use?** If something on your laptop already uses port 9900, start the kit with another port for the webhook receiver, for example `WEBHOOK_PORT=9950 docker compose up --build`.

## Simulating trouble

Each mock service has control endpoints. For example, for `claims` on port 7003:

| Command | Effect |
| --- | --- |
| `curl -X POST localhost:7003/control/down` | Health checks return 503 until reset |
| `curl -X POST "localhost:7003/control/slow?ms=3000"` | Health checks succeed, but only after 3 seconds |
| `curl -X POST "localhost:7003/control/flaky?rate=0.3"` | Health checks fail at random, 30% of the time |
| `curl -X POST localhost:7003/control/reset` | Back to normal |
| `curl localhost:7003/control/state` | Shows the current mode |

## The incident drill

`scripts/incident-drill.sh` breaks and repairs the services on a fixed schedule, so you can test Pulse and run a repeatable demo:

```
bash scripts/incident-drill.sh          # full drill, about 7 minutes
bash scripts/incident-drill.sh --fast   # short drill, under 3 minutes
bash scripts/incident-drill.sh --reset  # put every service back to normal
```

## Rules

- You may configure and run the kit however you like, but **don't change the mock services or the webhook receiver**: the evaluators test your product against the original kit.
