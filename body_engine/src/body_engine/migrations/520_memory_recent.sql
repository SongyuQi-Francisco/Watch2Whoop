-- Long-term memory (issue #112): observation notes get a kind. 'fact' = lasting fact (as before); 'recent' = current
-- circumstances that expire ("away on business until Friday", "day 3 of a cold"). Dates are the user's local dates,
-- valid_until inclusive. Expired recent notes stay (get_memory history, week reviews) but leave the per-conversation memory.
ALTER TABLE assistant_observations
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact', 'recent')),
  ADD COLUMN valid_from DATE,
  ADD COLUMN valid_until DATE,
  ADD CONSTRAINT assistant_observations_recent_until CHECK (kind = 'fact' OR valid_until IS NOT NULL),
  ADD CONSTRAINT assistant_observations_valid_range CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from);
CREATE INDEX assistant_observations_recent_idx ON assistant_observations (user_id, valid_until) WHERE kind = 'recent';
