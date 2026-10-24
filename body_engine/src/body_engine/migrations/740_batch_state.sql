-- hourly-batch (batch.py): what each step of the hourly / nightly job last did per user, so a user whose inputs did not
-- change since the last successful run is skipped. Only successful runs are recorded; losing the table is harmless
-- (a missing row = run the step).
CREATE TABLE IF NOT EXISTS batch_state (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  step TEXT NOT NULL,                      -- import-ow / compute-scores / evaluate-rules / refresh-status
  fingerprint TEXT NOT NULL,               -- hash of the inputs at the time of the last successful run
  ran_at TIMESTAMPTZ NOT NULL DEFAULT now(),       -- last time the step really ran
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- last time the job looked at it (ran, or skipped as unchanged): the doctor's freshness
  PRIMARY KEY (user_id, step)
);
