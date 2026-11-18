"""Integration: HTTP API + pages through FastAPI's TestClient, real Postgres, no background tasks."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.helpers import Clock, fail, feed, ok

INTERNAL_MARKERS = ["10.12.4.7", "payments-db", "upstream", "HTTP 503", "payments-team", "DB failover"]


@pytest.fixture
def app(engine, test_settings):
    return create_app(test_settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def operator(client):
    r = client.post("/api/auth/login", json={"username": "operator", "password": "pulse-demo"})
    assert r.status_code == 200
    return client


def payments_id(client) -> int:
    return next(s["id"] for s in client.get("/api/services").json() if s["name"] == "payments")


def make_outage(app, client, recover=False):
    c = Clock()
    results = [fail(c), fail(c)] + ([ok(c), ok(c), ok(c)] if recover else [])
    feed(app.state.sessions, payments_id(client), results)


# ---------------- auth & permissions ----------------
@pytest.mark.parametrize("method, path", [
    ("get", "/api/services"), ("post", "/api/services"), ("get", "/api/services/1"),
    ("patch", "/api/services/1"), ("get", "/api/services/1/checks"), ("get", "/api/incidents"),
    ("get", "/api/incidents/1"), ("post", "/api/incidents/1/acknowledge"), ("post", "/api/incidents/1/notes"),
    ("get", "/api/dashboard/summary"), ("get", "/api/metrics/summary"), ("get", "/api/services/1/metrics"),
])
def test_operator_api_requires_login(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_wrong_password_is_rejected(client):
    assert client.post("/api/auth/login", json={"username": "operator", "password": "nope"}).status_code == 401


def test_login_me_logout(operator):
    assert operator.get("/api/auth/me").json() == {"user": "operator"}
    operator.post("/api/auth/logout")
    assert operator.get("/api/auth/me").status_code == 401
    assert operator.get("/api/services").status_code == 401


def test_public_endpoints_need_no_login(client):
    assert client.get("/api/public/status").status_code == 200
    assert client.get("/api/public/history").status_code == 200


# ---------------- catalogue ----------------
def test_list_and_create_service_with_tier_defaults(operator):
    assert len(operator.get("/api/services").json()) == 4
    r = operator.post("/api/services", json={"name": "policies", "health_url": "http://policies:8080/health",
                                             "owner": "policy-team", "criticality": "high"})
    assert r.status_code == 201
    body = r.json()
    assert (body["check_interval_seconds"], body["current_status"], body["recovery_threshold"]) == (15, "UNKNOWN", 3)


def test_duplicate_or_invalid_service_is_rejected(operator):
    assert operator.post("/api/services", json={"name": "payments", "health_url": "http://x/h", "owner": "o"}
                         ).status_code == 422
    r = operator.post("/api/services", json={"name": "x", "health_url": "http://x/h", "owner": "o",
                                             "check_interval_seconds": 3, "timeout_ms": 5000})
    assert r.status_code == 422 and "timeout_ms must be shorter" in str(r.json())


def test_patch_is_validated_against_stored_values(operator):
    pid = payments_id(operator)
    # timeout 5000 ms is stored; shrinking the interval to 4 s alone would make timeout >= interval
    r = operator.patch(f"/api/services/{pid}", json={"check_interval_seconds": 4})
    assert r.status_code == 422
    r = operator.patch(f"/api/services/{pid}", json={"slow_threshold_ms": 2000, "failure_threshold": 3})
    assert r.status_code == 200 and r.json()["slow_threshold_ms"] == 2000


def test_unknown_service_is_404(operator):
    assert operator.get("/api/services/999").status_code == 404


# ---------------- incidents via API ----------------
def test_incident_ack_notes_and_timeline(app, operator):
    make_outage(app, operator)
    inc = operator.get("/api/incidents?status=active").json()[0]
    assert inc["code"] == "INC-0001" and inc["service_name"] == "payments" and inc["status"] == "OPEN"

    r = operator.post(f"/api/incidents/{inc['id']}/acknowledge")
    assert r.json()["status"] == "ACKNOWLEDGED" and r.json()["acknowledged_by"] == "operator"
    r = operator.post(f"/api/incidents/{inc['id']}/notes", json={"note": "DB failover in progress"})
    assert r.status_code == 201
    kinds = [e["kind"] for e in r.json()["timeline"]]
    assert kinds[:2] == ["detected", "opened"] and "acknowledged" in kinds and "note" in kinds
    # operators see the exact technical error in the timeline
    assert "10.12.4.7" in str(r.json()["timeline"])


def test_actions_on_resolved_incident_are_409(app, operator):
    make_outage(app, operator, recover=True)
    inc_id = operator.get("/api/incidents").json()[0]["id"]
    assert operator.post(f"/api/incidents/{inc_id}/acknowledge").status_code == 409
    assert operator.post(f"/api/incidents/{inc_id}/notes", json={"note": "x"}).status_code == 409
    assert operator.post("/api/incidents/999/acknowledge").status_code == 404


def test_dashboard_and_metrics(app, operator):
    make_outage(app, operator, recover=True)
    summary = operator.get("/api/dashboard/summary").json()
    assert summary["total_services"] == 4 and summary["by_status"]["HEALTHY"] == 1
    # one row per service with everything the dashboard screen shows
    row = next(r for r in summary["services"] if r["service"]["name"] == "payments")
    assert row["last_check"]["outcome"] == "OK_FAST" and len(row["recent"]) == 5
    assert row["reliability"]["availability"]["total_checks"] == 5 and summary["active_incidents"] == []
    m = operator.get("/api/metrics/summary").json()
    assert m["incidents_total"] == 1 and m["mttd_seconds"] is not None and m["mttr_seconds"] is not None
    sm = operator.get(f"/api/services/{payments_id(operator)}/metrics").json()
    assert sm["availability"]["total_checks"] == 5 and not sm["availability"]["sufficient"]


# ---------------- public safety (scenario 10) ----------------
def test_public_views_never_expose_internal_details(app, operator):
    make_outage(app, operator)
    inc_id = operator.get("/api/incidents").json()[0]["id"]
    operator.post(f"/api/incidents/{inc_id}/notes", json={"note": "DB failover in progress"})

    status = operator.get("/api/public/status").json()
    pay = next(s for s in status["services"] if s["name"] == "Payments")
    assert pay["status"] == "Service disruption" and pay["active_incidents"][0]["state"] == "Investigating"
    assert pay["active_incidents"][0]["latest_update"]["text"].startswith("Investigating")

    public_text = operator.get("/api/public/status").text + operator.get("/api/public/history").text
    for marker in INTERNAL_MARKERS:
        assert marker not in public_text, marker


# ---------------- restart (scenario 9) ----------------
def test_state_and_incidents_survive_an_app_restart(engine, test_settings):
    first = create_app(test_settings)
    with TestClient(first) as c:
        c.post("/api/auth/login", json={"username": "operator", "password": "pulse-demo"})
        make_outage(first, c)
        checks_before = len(c.get(f"/api/services/{payments_id(c)}/checks").json())

    second = create_app(test_settings)          # a brand-new process would do exactly this
    with TestClient(second) as c:
        c.post("/api/auth/login", json={"username": "operator", "password": "pulse-demo"})
        pay = next(s for s in c.get("/api/services").json() if s["name"] == "payments")
        assert (pay["current_status"], pay["consecutive_failures"]) == ("DOWN", 2)
        assert len(c.get("/api/incidents?status=active").json()) == 1
        assert len(c.get(f"/api/services/{pay['id']}/checks").json()) == checks_before
        assert len(c.get("/api/services").json()) == 4          # not seeded twice


# ---------------- backend-only entry point (D47) ----------------
def test_root_redirects_to_interactive_api_docs(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"
    assert client.get("/docs").status_code == 200
    assert "/api/public/status" in client.get("/openapi.json").text
