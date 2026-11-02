-- Each person's own look of the Today page (issue #126, flag slim_mode; appearance/store.py): which theme (default / slim =
-- 薄肌模式) and whether the daily quote shows. No row = default theme, quote on. Only the person reads or writes their row.
-- No grant to grafana_ro (not health data).
CREATE TABLE user_appearance (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  theme TEXT NOT NULL DEFAULT 'default' CHECK (theme IN ('default', 'slim')),
  quotes BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
