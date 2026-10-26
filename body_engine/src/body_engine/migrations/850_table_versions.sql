-- Change stamps of shared, rarely written tables that the API keeps in process memory (training/catalog.py catalog_rows).
-- Checking "did exercise_catalog change?" used to be SELECT count(*), max(updated_at) over the whole table, run once per
-- exercise lookup: with the ~6,300-row library that was ~20 full scans and ~0.3 s per Today page. A statement-level
-- trigger now bumps one row here on every write, so the check is one primary-key read.
CREATE TABLE IF NOT EXISTS table_versions (
  name TEXT PRIMARY KEY,
  version BIGINT NOT NULL DEFAULT 0,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE OR REPLACE FUNCTION bump_table_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO table_versions (name, version, changed_at) VALUES (TG_TABLE_NAME, 1, clock_timestamp())
  ON CONFLICT (name) DO UPDATE SET version = table_versions.version + 1, changed_at = clock_timestamp();
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS exercise_catalog_version ON exercise_catalog;
CREATE TRIGGER exercise_catalog_version AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON exercise_catalog
  FOR EACH STATEMENT EXECUTE FUNCTION bump_table_version();

INSERT INTO table_versions (name) VALUES ('exercise_catalog') ON CONFLICT DO NOTHING;
