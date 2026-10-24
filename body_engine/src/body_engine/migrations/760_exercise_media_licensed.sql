-- Licensed exercise demo media (issue #248, docs/exercise-library-quality.md): pictures whose licence we checked, with the credit
-- each one needs. Separate from `images` (the free-exercise-db pictures, whose licence is unclear): the rollout flag
-- exercise_media_licensed decides which ones the page shows. Filled by `body-engine exercises-media-fetch`; the seed and the
-- openGym import never touch it.
--   media: [{"file": "licensed/<source>/<name>", "source": "wger" | "everkinetic", "source_id", "license", "license_url",
--            "author", "origin_url"}, ...]
-- exercise_catalog already has SELECT for grafana_ro (a table-level grant covers new columns).
ALTER TABLE exercise_catalog ADD COLUMN media JSONB NOT NULL DEFAULT '[]';
