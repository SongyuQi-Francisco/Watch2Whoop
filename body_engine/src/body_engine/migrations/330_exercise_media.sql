-- Issue #41: free-exercise-db import merged into the curated catalog, demo images in the deployment's own media storage.
-- source_ref: upstream free-exercise-db id (e.g. "Dumbbell_Bench_Press") of the row, or of the upstream entry merged into a curated row.
-- family_slug: curated exercise this imported variant belongs to (e.g. "Upright Cable Row" -> upright_row); a user's
--   restriction on the curated exercise applies to the variant too (training/catalog.py, training/params.HAZARD_FAMILIES).
ALTER TABLE exercise_catalog ADD COLUMN source_ref TEXT, ADD COLUMN family_slug TEXT;
CREATE INDEX exercise_catalog_source_ref_idx ON exercise_catalog (source_ref);
