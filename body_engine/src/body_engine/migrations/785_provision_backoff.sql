-- Self-signup provisioning retries with backoff (account/provision.py): after a failed run the next try waits
-- RETRY_BASE * 2^(attempts-1), at most RETRY_MAX; `body-engine provision-run` only picks rows that are due.
-- NULL = due now (new rows, rows written before this migration). Not health data; no grant to grafana_ro.
ALTER TABLE user_provisioning ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;
