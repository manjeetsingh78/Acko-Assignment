-- Runs once, when the Postgres volume is first created.
-- A separate database for pytest, so tests never touch demo data.
CREATE DATABASE pulse_test;
