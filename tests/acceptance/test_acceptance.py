"""Acceptance tests: the 10 scenarios from the brief, against the REAL running stack.

    docker compose up -d --build
    pytest -m acceptance            (about 3 minutes)

The tests break services with the kit's own control endpoints, watch Pulse react through its API,
and check the kit's webhook receiver. To keep them quick they first PATCH every service to a 2 s
interval (which also exercises live config changes) and restore the original config at the end.
"""
import os
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.acceptance

PULSE = os.environ.get("PULSE_URL", "http://127.0.0.1:8000")
RECEIVER = os.environ.get("RECEIVER_URL", "http://127.0.0.1:9900")
KIT_PORTS = {"quotes": 7001, "payments": 7002, "claims": 7003, "notifications": 7004}
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT", "ackoassignment")
FAST = {"check_interval_seconds": 2, "timeout_ms": 1500, "slow_threshold_ms": 500,
        "flaky_failure_count": 3, "flaky_window_seconds": 20, "failure_threshold": 2, "recovery_threshold": 3}
CONFIG_KEYS = list(FAST)


def kit(service: str, action: str, query: str = "") -> None:
    url = f"http://127.0.0.1:{KIT_PORTS[service]}/control/{action}" + (f"?{query}" if query else "")
    httpx.post(url, timeout=5).raise_for_status()


def wait_for(predicate, timeout_s: float = 30, every_s: float = 0.3, what: str = "condition"):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(every_s)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {what}")


class Pulse:
    def __init__(self):
        self.http = httpx.Client(base_url=PULSE, timeout=10)
        self.login()

    def login(self):
        r = self.http.post("/api/auth/login", json={"username": os.environ.get("PULSE_OPERATOR_USERNAME", "operator"),
                                                    "password": os.environ.get("PULSE_OPERATOR_PASSWORD", "pulse-demo")})
        r.raise_for_status()

    def services(self) -> dict:
        return {s["name"]: s for s in self.http.get("/api/services").json()}

    def service(self, name: str) -> dict:
        return self.services()[name]

    def incidents(self, name: str) -> list[dict]:
        sid = self.service(name)["id"]
        return self.http.get(f"/api/incidents?service_id={sid}").json()

    def patch(self, name: str, **changes):
        r = self.http.patch(f"/api/services/{self.service(name)['id']}", json=changes)
        assert r.status_code == 200, r.text

    def status_is(self, name: str, *statuses: str):
        return self.service(name)["current_status"] in statuses


def received(event: str | None = None, incident_code: str | None = None) -> list[dict]:
    items = [i["body"] for i in httpx.get(f"{RECEIVER}/api/received", timeout=5).json() if isinstance(i["body"], dict)]
    return [b for b in items if (event is None or b.get("event") == event)
            and (incident_code is None or b.get("incident_id") == incident_code)]


@pytest.fixture(scope="module")
def pulse():
    try:
        httpx.get(f"{PULSE}/healthz", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("Pulse is not running: start it with `docker compose up -d --build`")
    p = Pulse()
    original = {name: {k: s[k] for k in CONFIG_KEYS} for name, s in p.services().items()}
    for name in KIT_PORTS:
        kit(name, "reset")
        # order matters: shrink timeout/slow first so every intermediate config stays valid
        p.patch(name, **FAST)
    httpx.delete(f"{RECEIVER}/api/received", timeout=5)
    yield p
    for name in KIT_PORTS:
        kit(name, "reset")
    for name, cfg in original.items():
        if name in KIT_PORTS:
            p.patch(name, **cfg)


def recover(pulse: Pulse, name: str):
    kit(name, "reset")
    wait_for(lambda: pulse.status_is(name, "HEALTHY"), 30, what=f"{name} HEALTHY")


# ---------------- scenarios ----------------
def test_1_all_services_healthy(pulse):
    wait_for(lambda: all(s["current_status"] == "HEALTHY" for s in pulse.services().values()), 30,
             what="all services HEALTHY")


def test_2_slow_service_is_degraded_without_incident(pulse):
    before = len(pulse.incidents("payments"))
    kit("payments", "slow", "ms=1000")          # 500 ms slow threshold < 1000 ms < 1500 ms timeout
    svc = wait_for(lambda: (s := pulse.service("payments"))["current_status"] == "DEGRADED" and s, 20,
                   what="payments DEGRADED")
    assert svc["status_reason"] == "slow_response"
    time.sleep(4)                                # a few more slow checks
    assert pulse.service("payments")["current_status"] == "DEGRADED"
    assert len(pulse.incidents("payments")) == before
    recover(pulse, "payments")


def test_3_single_failure_is_degraded_without_incident(pulse):
    before = len(pulse.incidents("claims"))
    sid = pulse.service("claims")["id"]
    last_id = pulse.http.get(f"/api/services/{sid}/checks?limit=1").json()[0]["id"]
    kit("claims", "down")

    def first_failure():
        checks = pulse.http.get(f"/api/services/{sid}/checks?limit=3").json()
        return any(c["id"] > last_id and not c["success"] for c in checks)
    wait_for(first_failure, 10, every_s=0.1, what="first failed check")
    kit("claims", "reset")                       # repaired before the 2nd check
    svc = pulse.service("claims")
    assert svc["current_status"] == "DEGRADED" and svc["consecutive_failures"] == 1
    recover(pulse, "claims")
    assert len(pulse.incidents("claims")) == before


def test_4_5_8_10_outage_incident_webhooks_recovery_and_public_safety(pulse):
    kit("claims", "down")
    wait_for(lambda: pulse.status_is("claims", "DOWN"), 20, what="claims DOWN")
    active = [i for i in pulse.incidents("claims") if i["status"] != "RESOLVED"]
    assert len(active) == 1                                         # scenario 4
    code = active[0]["code"]
    wait_for(lambda: received("incident.opened", code), 15, what="opened webhook")    # scenario 8

    # scenario 10: customers see a friendly message, never the internal error
    public = httpx.get(f"{PULSE}/api/public/status").text + httpx.get(f"{PULSE}/status").text
    assert "Service disruption" in public
    for secret in ("10.12.4.7", "claims-db", "upstream", "503"):
        assert secret not in public
    detail = pulse.http.get(f"/api/incidents/{active[0]['id']}").json()
    assert "10.12.4.7" in str(detail["timeline"])                   # ...but engineers do see it

    # scenario 5: needs 3 consecutive successes, passing through RECOVERING
    kit("claims", "reset")
    wait_for(lambda: pulse.status_is("claims", "RECOVERING"), 10, every_s=0.1, what="claims RECOVERING")
    assert pulse.http.get(f"/api/incidents/{active[0]['id']}").json()["status"] != "RESOLVED"
    wait_for(lambda: pulse.status_is("claims", "HEALTHY"), 20, what="claims HEALTHY")
    assert pulse.http.get(f"/api/incidents/{active[0]['id']}").json()["status"] == "RESOLVED"
    wait_for(lambda: received("incident.resolved", code), 15, what="resolved webhook")
    time.sleep(3)
    assert len(received("incident.opened", code)) == 1 and len(received("incident.resolved", code)) == 1
    assert "10.12.4.7" not in str(received(incident_code=code))


def test_6_flaky_service_is_degraded_with_reason_and_no_flaky_incident(pulse):
    pulse.patch("quotes", failure_threshold=5)   # isolate flaky detection from the 2-in-a-row DOWN rule
    try:
        kit("quotes", "flaky", "rate=0.5")
        wait_for(lambda: pulse.service("quotes")["status_reason"] == "flaky", 60, what="quotes flaky")
        assert pulse.service("quotes")["current_status"] == "DEGRADED"
        assert all(i["trigger_type"] == "service_down" for i in pulse.incidents("quotes"))
    finally:
        kit("quotes", "reset")
        pulse.patch("quotes", failure_threshold=2)
    wait_for(lambda: pulse.status_is("quotes", "HEALTHY"), 40, what="quotes HEALTHY after flaky window")


def test_7_multiple_incidents_without_duplicates(pulse):
    before = len(pulse.incidents("claims"))
    for _ in range(2):
        kit("claims", "down")
        wait_for(lambda: pulse.status_is("claims", "DOWN"), 20, what="claims DOWN")
        time.sleep(5)                            # several more failed checks: must NOT add incidents
        recover(pulse, "claims")
    new = pulse.incidents("claims")[: len(pulse.incidents("claims")) - before]
    assert len(new) == 2
    assert len({i["trigger_key"] for i in new}) == 2


@pytest.mark.skipif(os.environ.get("SKIP_RESTART") == "1", reason="restart test disabled")
def test_9_restart_keeps_history_and_open_incidents(pulse):
    kit("claims", "down")
    wait_for(lambda: pulse.status_is("claims", "DOWN"), 20, what="claims DOWN")
    open_before = [i["id"] for i in pulse.incidents("claims") if i["status"] != "RESOLVED"]
    sid = pulse.service("claims")["id"]
    checks_before = len(pulse.http.get(f"/api/services/{sid}/checks?limit=1000").json())

    subprocess.run(["docker", "compose", "-p", COMPOSE_PROJECT, "restart", "pulse"], check=True,
                   capture_output=True, timeout=120)
    wait_for(lambda: _healthy(), 60, what="Pulse back up")
    pulse.login()                                # cookies survive, but be explicit

    open_after = [i["id"] for i in pulse.incidents("claims") if i["status"] != "RESOLVED"]
    assert open_after == open_before            # same incident, not lost and not duplicated
    assert pulse.service("claims")["current_status"] == "DOWN"
    wait_for(lambda: len(pulse.http.get(f"/api/services/{sid}/checks?limit=1000").json()) > checks_before + 1,
             20, what="checks resumed")
    recover(pulse, "claims")
    assert all(i["status"] == "RESOLVED" for i in pulse.incidents("claims") if i["id"] in open_before)


def _healthy() -> bool:
    try:
        return httpx.get(f"{PULSE}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False
