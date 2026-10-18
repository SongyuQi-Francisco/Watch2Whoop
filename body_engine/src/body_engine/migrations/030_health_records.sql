-- Health records: supplements, lab results, journal, body events, exercise extras (docs/system-design.md §6, §7).
-- import_key: stable key for migration / import scripts, (user_id, import_key) is unique so re-imports create no duplicates; NULL for manually added rows.

-- Supplements. status: active = taking / as_needed / paused / stopped / not_needed = evaluated, not needed
ALTER TABLE supplements
  ADD COLUMN advice TEXT,                  -- current advice (e.g. stop before the follow-up test), separate from rationale (why take it)
  ADD COLUMN import_key TEXT,
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD CONSTRAINT supplements_import_key_uq UNIQUE (user_id, import_key);
ALTER TABLE supplements DROP CONSTRAINT supplements_status_check;
ALTER TABLE supplements ADD CONSTRAINT supplements_status_check
  CHECK (status IN ('active', 'as_needed', 'paused', 'stopped', 'not_needed'));

-- Lab results / checkups. test_key: English key unified across reports (e.g. uric_acid), used to view trends over time.
-- flag: H high / L low / abnormal (non-numeric items) / normal; NULL = the report did not flag it.
ALTER TABLE lab_results
  ADD COLUMN test_key TEXT,
  ADD COLUMN category TEXT,                -- blood / imaging / ecg / anthropometry / urine / other
  ADD COLUMN import_key TEXT,
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD CONSTRAINT lab_results_import_key_uq UNIQUE (user_id, import_key),
  ADD CONSTRAINT lab_results_flag_check CHECK (flag IS NULL OR flag IN ('H', 'L', 'abnormal', 'normal'));
CREATE INDEX lab_results_test_idx ON lab_results (user_id, test_key, collected_on);

-- Body events. time_precision: minute = exact time / day = only the day is known (stored as 12:00 that day) / approx = roughly that day / month = only the month is known
ALTER TABLE events
  ADD COLUMN time_precision TEXT NOT NULL DEFAULT 'minute'
    CHECK (time_precision IN ('minute', 'day', 'approx', 'month')),
  ADD COLUMN resolved_on DATE,             -- date it got better / stopped recurring; NULL = still ongoing or unknown
  ADD COLUMN import_key TEXT,
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD CONSTRAINT events_import_key_uq UNIQUE (user_id, import_key),
  ADD CONSTRAINT events_severity_check CHECK (severity IS NULL OR severity BETWEEN 1 AND 5);
CREATE INDEX events_user_time_idx ON events (user_id, occurred_at);

-- Journal. kind: bool (yes 1 / no 0) or number (quantity with a unit). Date = the day the behavior happened; outcomes are read on the next day.
ALTER TABLE journal_items
  ADD COLUMN category TEXT,                -- supplement / diet / lifestyle / training / symptom
  ADD COLUMN description TEXT,
  ADD COLUMN sort INT NOT NULL DEFAULT 100,
  ADD CONSTRAINT journal_items_kind_check CHECK (kind IN ('bool', 'number'));
ALTER TABLE journal_entries
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX journal_entries_key_idx ON journal_entries (user_id, key, date);

-- Exercise extras. exercise_ref starts as a stable English slug (e.g. upright_row);
-- app_exercise_id = the exercise ID in the logging app (SparkyFitness), filled in once matched, (user_id, app_exercise_id) is unique.
ALTER TABLE exercise_meta
  ADD COLUMN name_en TEXT,
  ADD COLUMN aliases TEXT[] NOT NULL DEFAULT '{}',   -- other names, to help match exercise names in the logging app
  ADD COLUMN reason TEXT,                  -- why banned / caution
  ADD COLUMN app_exercise_id TEXT,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD CONSTRAINT exercise_meta_app_id_uq UNIQUE (user_id, app_exercise_id);
