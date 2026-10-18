-- Long-term memory and screenshot metrics of the AI assistant "body butler" (docs/ai-assistant-plan.md §2.2).
-- Memory is the assistant's own notes: the user can view and edit the profile card and delete observations on the Today page; every change by the assistant is written to assistant_audit.

-- Body profile card: one set of key-values per person (goals, constraints, routine, preferences, what works / doesn't, current plan...), read at the start of every conversation.
-- source: seed (memory-init generates from the database and body-management.md) / assistant (updated by the butler) / user (edited by the user on the Today page)
CREATE TABLE assistant_profile (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*$'),
  label TEXT NOT NULL,
  value TEXT NOT NULL,
  sort INT NOT NULL DEFAULT 100,
  source TEXT NOT NULL CHECK (source IN ('seed', 'assistant', 'user')),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

-- Observation notes: new facts learned in conversation (e.g. plays basketball every Wednesday evening, lactose intolerance), with source and date; deleting is a real delete.
CREATE TABLE assistant_observations (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text TEXT NOT NULL CHECK (length(btrim(text)) > 0),
  source TEXT NOT NULL CHECK (source IN ('seed', 'assistant', 'user')),
  evidence TEXT,                         -- basis: the user's own words / which data
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX assistant_observations_user_idx ON assistant_observations (user_id, created_at);
CREATE UNIQUE INDEX assistant_observations_text_uq ON assistant_observations (user_id, md5(btrim(text)));

-- Metrics read from screenshots (Apple Readiness / sleep score, InBody segment data, etc. that third parties cannot read), for comparison only.
-- A separate table, not written into daily_metrics, so it never overwrites data from sensor sources (ow / apple_export / shortcut).
CREATE TABLE metric_snapshots (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,                    -- the day the metric belongs to (sleep score = the day of waking up)
  metric TEXT NOT NULL CHECK (metric ~ '^[a-z][a-z0-9_]*$'),
  value DOUBLE PRECISION NOT NULL,
  unit TEXT,
  source TEXT NOT NULL DEFAULT 'screenshot',
  source_app TEXT NOT NULL,              -- which app / device the screenshot came from, e.g. apple_health, inbody
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, date, metric, source_app)
);
