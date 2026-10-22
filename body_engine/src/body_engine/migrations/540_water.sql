-- Daily water intake (issue #125, water/): one row per drink. Sources: the user's AI (log_water), the Today page's
-- quick buttons, manual entry, and Apple Health (HKQuantityTypeIdentifierDietaryWater, uploaded by the app to
-- POST /v1/ingest/healthkit; Open Wearables 0.9 does not carry Apple's water). Daily totals are summed per local_date.
-- Idempotent: a page / AI retry with the same client_id, or a HealthKit sample with the same UUID, is stored once.
CREATE TABLE IF NOT EXISTS water_intake (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  at TIMESTAMPTZ NOT NULL,
  local_date DATE NOT NULL,                 -- date of `at` in the user's time zone
  ml INT NOT NULL CHECK (ml > 0 AND ml <= 5000),
  source TEXT NOT NULL CHECK (source IN ('ai', 'manual', 'button', 'apple_health')),
  client_id TEXT,                           -- idempotency key from the page / AI call
  hk_uuid UUID,                             -- HealthKit sample UUID (apple_health)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS water_intake_user_date_idx ON water_intake (user_id, local_date);
CREATE UNIQUE INDEX IF NOT EXISTS water_intake_client_idx ON water_intake (user_id, client_id) WHERE client_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS water_intake_hk_idx ON water_intake (user_id, hk_uuid) WHERE hk_uuid IS NOT NULL;
