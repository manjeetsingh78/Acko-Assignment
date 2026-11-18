-- 001: initial schema (PLAN.md section 7)
-- All timestamps are UTC ISO-8601 strings, e.g. 2026-11-18T12:00:00.000Z

CREATE TABLE services (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    health_url       TEXT    NOT NULL,
    interval_s       INTEGER NOT NULL,
    timeout_ms       INTEGER NOT NULL,
    owner            TEXT    NOT NULL,
    slo_target       REAL    NOT NULL CHECK (slo_target > 0 AND slo_target <= 100),
    state            TEXT    NOT NULL DEFAULT 'unknown'
                     CHECK (state IN ('unknown', 'healthy', 'degraded', 'down')),
    state_since      TEXT,
    consec_failures  INTEGER NOT NULL DEFAULT 0,
    consec_successes INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    CHECK (timeout_ms < interval_s * 1000)
);

CREATE TABLE check_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    scheduled_at TEXT    NOT NULL,          -- when the check should have run
    checked_at   TEXT    NOT NULL,          -- when it actually ran (lag = difference)
    ok           INTEGER NOT NULL,          -- 1 = 2xx within timeout
    slow         INTEGER NOT NULL DEFAULT 0,-- 1 = ok but over the slow threshold
    status_code  INTEGER,
    latency_ms   INTEGER,
    error        TEXT                       -- INTERNAL ONLY: never render publicly
);
CREATE INDEX idx_checks_service_time ON check_results(service_id, checked_at);

CREATE TABLE incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT    NOT NULL UNIQUE,      -- e.g. INC-001
    service_id       INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    status           TEXT    NOT NULL CHECK (status IN ('open', 'acknowledged', 'resolved')),
    first_failure_at TEXT    NOT NULL,             -- for MTTD
    opened_at        TEXT    NOT NULL,
    acknowledged_at  TEXT,
    acknowledged_by  TEXT,
    resolved_at      TEXT,
    public_message   TEXT    NOT NULL
);
-- D9: at most one unresolved incident per service, enforced by the database too.
CREATE UNIQUE INDEX one_open_incident_per_service
    ON incidents(service_id) WHERE status != 'resolved';

CREATE TABLE incident_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    at          TEXT    NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN
                ('opened', 'failure', 'acknowledged', 'note', 'recovering', 'resolved', 'notified')),
    actor       TEXT,
    message     TEXT    NOT NULL
);
CREATE INDEX idx_events_incident ON incident_events(incident_id, at);

CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event       TEXT    NOT NULL CHECK (event IN ('opened', 'resolved')),
    attempt     INTEGER NOT NULL,
    sent_at     TEXT    NOT NULL,
    http_status INTEGER,
    success     INTEGER NOT NULL,
    error       TEXT
);
