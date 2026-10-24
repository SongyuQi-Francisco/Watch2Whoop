-- Exercise library from openGym (issue #248): English description / steps, a licence per row and where each text came from.
--   license:         licence of the row's own text (MIT for ExerciseDB-origin entries, AGPL-3.0 for openGym's own, Unlicense for
--                    free-exercise-db, AGPL-3.0 for the curated rows of this repository).
--   description_en / instructions_en: English text; the Chinese columns stay empty for imported rows until a translation is added.
--   text_sources:    [{source, license, ref, commit, body_part, target, category, equipment}, ...] every upstream entry whose text
--                    went into the row (an existing row that an openGym entry was merged into gets one more element).
-- exercise_catalog already has SELECT for grafana_ro (a table-level grant covers new columns).
ALTER TABLE exercise_catalog
  ADD COLUMN license TEXT,
  ADD COLUMN description_en TEXT,
  ADD COLUMN instructions_en TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN text_sources JSONB NOT NULL DEFAULT '[]';

UPDATE exercise_catalog SET license = CASE source WHEN 'free-exercise-db' THEN 'Unlicense' WHEN 'curated' THEN 'AGPL-3.0' END
 WHERE license IS NULL;
