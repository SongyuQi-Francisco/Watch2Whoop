-- Muscle load map (rollout flag muscle_load): what the user wants to build and the physique goal, set in 我 → 训练设置.
-- focus_muscles = catalog muscle keys (training/catalog_data.MUSCLE_ZH); the AI reads them with the muscle load (get_muscle_load).
-- The physique goal sentence is also written to the AI's long-term memory (profile card item physique_goal), see training/goals.py.
CREATE TABLE IF NOT EXISTS training_goals (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  focus_muscles TEXT[] NOT NULL DEFAULT '{}',
  physique_goal TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT ON training_goals TO grafana_ro;
