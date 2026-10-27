-- The conditions of a weigh-in (tags such as fasting / after exercise, a short note, when it was measured), next to the weight
-- itself (SparkyFitness check-in / body_measurements, one weight per day). Kept apart from body_measurements so the double-write
-- sync of the weights never touches it. Keys of tags: weightlog/params.py.
CREATE TABLE weight_context (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  note TEXT,
  measured_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date)
);
