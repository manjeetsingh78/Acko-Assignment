"""Shared test fixtures.

Database tests run against a real PostgreSQL (the Compose `db` container, database
`pulse_test`), not SQLite, so constraints behave exactly as in production.
Start it with:  docker compose up -d db
"""
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.models import Base

DATABASE_HOST = os.environ.get(
    "PULSE_TEST_DATABASE_URL", "postgresql+psycopg://pulse:pulse@127.0.0.1:5433/pulse_test")


@pytest.fixture(scope="session")
def test_db_url():
    """A throwaway database for this pytest session.

    Every test drops and recreates the schema, so two pytest processes sharing one
    database race their DDL (drop_all vs create_all = deadlocks and pg_type clashes).
    A unique database per session makes parallel or overlapping runs safe. Set
    PULSE_TEST_DATABASE_URL to force a specific database instead.
    """
    override = os.environ.get("PULSE_TEST_DATABASE_URL")
    if override:
        yield override
        return
    dbname = f"pulse_test_{uuid.uuid4().hex[:8]}"
    admin = make_engine("postgresql+psycopg://pulse:pulse@127.0.0.1:5433/pulse")
    try:
        try:
            with admin.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        except OperationalError:
            pytest.skip("PostgreSQL not reachable on :5433 - run `docker compose up -d db`")
        yield f"postgresql+psycopg://pulse:pulse@127.0.0.1:5433/{dbname}"
    finally:
        try:
            with admin.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        finally:
            admin.dispose()


@pytest.fixture
def test_settings(test_db_url) -> Settings:
    return Settings(database_url=test_db_url, run_background=False)


@pytest.fixture
def engine(test_db_url):
    eng = make_engine(test_db_url)
    try:
        Base.metadata.drop_all(eng)   # every test starts from empty tables
    except OperationalError:
        pytest.skip("PostgreSQL not reachable on :5433 - run `docker compose up -d db`")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with make_session_factory(engine)() as s:
        yield s
