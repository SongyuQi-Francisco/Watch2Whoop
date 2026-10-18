-- Grafana read-only role (docs/system-design.md §2, §9; deployed in deploy/grafana/).
-- This only creates the role (NOLOGIN, no password) and grants: migration files go into git and must not hold passwords.
-- deploy/grafana/bootstrap.py then runs ALTER ROLE grafana_ro LOGIN PASSWORD (password in secrets/grafana_ro_body_password).
-- The role is shared by the whole Postgres instance: if it exists it is not recreated (tests create temporary databases and run this migration too), and an already-set LOGIN is not reverted.
-- Grants only the tables the dashboards use; api_tokens, assistant_audit, shares, supplements, lab_results, events, exercise_meta etc. are not granted.
-- When a dashboard needs a new table later: add a new migration with GRANT, do not use ALTER DEFAULT PRIVILEGES (new tables would become visible to Grafana automatically).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
  END IF;
END $$;

ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '30s';
ALTER ROLE grafana_ro SET TimeZone = 'Australia/Sydney';

REVOKE ALL ON SCHEMA public FROM grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON daily_metrics, sleep_sessions, daily_scores, journal_items, journal_entries,
  reminders, rule_runs, sync_state TO grafana_ro;
-- users: only the columns dashboard variables need (not oidc_sub, sparky_user_id, etc.)
GRANT SELECT (id, display_name, timezone, ow_user_id) ON users TO grafana_ro;
