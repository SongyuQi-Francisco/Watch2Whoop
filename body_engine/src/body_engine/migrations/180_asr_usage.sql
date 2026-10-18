-- Voice -> text for Jarvis (ai/asr.py, the local asr container): one row per recognition, used for the per-person daily / per-minute limits.
-- Only durations: no audio and no recognised text are stored anywhere.
CREATE TABLE asr_usage (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  audio_ms INT NOT NULL,
  proc_ms INT NOT NULL,
  ok BOOLEAN NOT NULL,
  error TEXT
);
CREATE INDEX asr_usage_user_idx ON asr_usage (user_id, at);
