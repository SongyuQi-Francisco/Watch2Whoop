-- Static data bundle (issue #248, item 2): the exercise library and other rarely-changing data as a versioned package the app keeps
-- locally and updates by version number (static_bundle/).
-- One global counter orders every change: a row's `rev` is the counter value of its last change; the bundle version is the highest rev.
--   exercise_catalog.rev   set by a trigger, only when the row's content really changes (re-seeding identical data keeps the rev);
--   static_tombstones      a deleted catalog row (module, key) with the rev of the deletion, so a client behind it can remove the row
--                          (a row that comes back clears its tombstone);
--   static_modules         the code-derived modules (taxonomy, glossary, split templates): content hash and the rev at which it changed.
-- Nothing here is personal data: the bundle only carries the shared catalog and static texts.
CREATE SEQUENCE static_data_rev_seq;

ALTER TABLE exercise_catalog ADD COLUMN rev BIGINT NOT NULL DEFAULT 0;

-- existing rows get their first revs in a stable order, before the trigger exists
UPDATE exercise_catalog c SET rev = o.n FROM (
  SELECT slug, nextval('static_data_rev_seq') AS n FROM (SELECT slug FROM exercise_catalog ORDER BY slug) s
) o WHERE c.slug = o.slug;

CREATE TABLE static_tombstones (
  module TEXT NOT NULL,
  key TEXT NOT NULL,
  rev BIGINT NOT NULL,
  PRIMARY KEY (module, key)
);
CREATE INDEX static_tombstones_rev_idx ON static_tombstones (rev);

CREATE TABLE static_modules (
  name TEXT PRIMARY KEY,
  hash TEXT NOT NULL,
  rev BIGINT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION exercise_catalog_rev() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    DELETE FROM static_tombstones WHERE module = 'exercises' AND key = NEW.slug;
    NEW.rev := nextval('static_data_rev_seq');
  ELSIF (to_jsonb(NEW) - 'rev' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'rev' - 'updated_at') THEN
    NEW.rev := nextval('static_data_rev_seq');
  ELSE
    NEW.rev := OLD.rev;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER exercise_catalog_rev_trg BEFORE INSERT OR UPDATE ON exercise_catalog
  FOR EACH ROW EXECUTE FUNCTION exercise_catalog_rev();

CREATE FUNCTION exercise_catalog_tombstone() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO static_tombstones (module, key, rev) VALUES ('exercises', OLD.slug, nextval('static_data_rev_seq'))
  ON CONFLICT (module, key) DO UPDATE SET rev = EXCLUDED.rev;
  RETURN OLD;
END $$;

CREATE TRIGGER exercise_catalog_tombstone_trg AFTER DELETE ON exercise_catalog
  FOR EACH ROW EXECUTE FUNCTION exercise_catalog_tombstone();

GRANT SELECT ON static_tombstones, static_modules TO grafana_ro;
