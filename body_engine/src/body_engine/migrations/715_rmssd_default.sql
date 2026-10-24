-- RMSSD becomes the default HRV kind for recovery (issue #117, owner decision 2026-10-24): scores/hrv_kind.py now reads
-- "no row" as switched on, and new rows default to true. Users still need enough beat data (RMSSD_* thresholds) or they
-- stay on SDNN; `body-engine hrv-kind --disable-rmssd` switches one person back. The kind changes at the next full
-- compute-scores (nightly), which rewrites that person's whole history.
ALTER TABLE score_settings ALTER COLUMN rmssd_enabled SET DEFAULT true;
