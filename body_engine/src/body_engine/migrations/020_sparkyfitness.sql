-- Mapping to SparkyFitness (training, food and weight logging). Body Engine reads data through the SparkyFitness API (sparky_client.py),
-- one read-only API key per person, kept in secrets/sparkyfitness_api_key_<sparky_key_name>_ro (not in the database, not in git).
ALTER TABLE users ADD COLUMN IF NOT EXISTS sparky_user_id TEXT UNIQUE;   -- user id inside SparkyFitness (uuid)
ALTER TABLE users ADD COLUMN IF NOT EXISTS sparky_key_name TEXT;         -- identifier in the secrets file name, e.g. owner
