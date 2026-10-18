-- Rule reminders (docs/system-design.md "rule engine" section; rules/ module). Adds a table only, no change to existing tables.
CREATE TABLE reminders (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  date DATE NOT NULL,                       -- the day the reminder is for (user's local date)
  rule_key TEXT NOT NULL,                   -- progression / calorie_adjust / pre_workout_fuel / frequency_caps / exercise_restrictions / readiness
  subject TEXT NOT NULL DEFAULT '',         -- tells apart several rows of one rule on one day (e.g. exercise ID:weight:up)
  level TEXT NOT NULL CHECK (level IN ('info', 'warning', 'alert')),
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',     -- supporting data (sets, weekly average weight, food log, scores...) and sources
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'seen', 'dismissed')),
  rules_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status_changed_at TIMESTAMPTZ,
  UNIQUE (user_id, date, rule_key, subject)
);
CREATE INDEX reminders_user_date ON reminders (user_id, date DESC);

-- Result of each evaluate-rules run: per rule triggered / not_triggered / insufficient_data / error, with the reason
CREATE TABLE rule_runs (
  user_id INT NOT NULL REFERENCES users(id),
  date DATE NOT NULL,
  ran_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  rules_version TEXT NOT NULL,
  results JSONB NOT NULL,
  PRIMARY KEY (user_id, date)
);
