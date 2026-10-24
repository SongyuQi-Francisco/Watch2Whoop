-- Bilingual exercise library (issue #248): where each row's Chinese came from.
--   zh_source: 'human'   = written by a person (the curated library and every row that already had Chinese); imports never overwrite it;
--              'machine' = from the offline batch translation (training/translations/exercises_zh.jsonl), shown only while the
--                          rollout flag exercise_zh_machine is on for the viewer;
--              NULL      = no Chinese yet (name_zh still holds the English name).
-- exercise_catalog already has SELECT for grafana_ro (a table-level grant covers new columns).
ALTER TABLE exercise_catalog
  ADD COLUMN zh_source TEXT CHECK (zh_source IS NULL OR zh_source IN ('human', 'machine'));

UPDATE exercise_catalog SET zh_source = 'human' WHERE name_zh ~ '[一-鿿]';
