-- Grafana read-only role in the Open Wearables database. OW table schemas are managed by its own alembic (vendor/ is not modified),
-- so the role and grants live here and are run by deploy/grafana/bootstrap.py (idempotent, safe to re-run); bootstrap.py also sets the password.
-- If tables are rebuilt after an OW upgrade the grants are lost: re-run bootstrap.py (the doctor row 「Grafana 数据源」 ("Grafana data sources") reports it).
-- Only tables the dashboards use: samples, types, sources (devices), sleep / workout records, sync records.
-- Not granted: user (email etc.), api_key, developer, invitation, refresh_token, user_connection, etc.
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
GRANT SELECT ON data_point_series, series_type_definition, data_source, event_record, sleep_details,
  workout_details, sync_run TO grafana_ro;
