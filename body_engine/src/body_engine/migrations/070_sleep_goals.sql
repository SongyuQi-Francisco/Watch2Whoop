-- Bedtime goal (docs/system-design.md §13 "bedtime goal"; goals/ module). Adds a table only. One current goal per person; changing the goal = re-setting the starting point.
CREATE TABLE sleep_goals (
  user_id INT PRIMARY KEY REFERENCES users(id),
  target_bedtime TIME NOT NULL,             -- final target bedtime (local time, e.g. 00:00)
  target_wake TIME,                         -- optional: target wake time; empty = tonight's target + sleep duration
  target_duration_min INT CHECK (target_duration_min BETWEEN 240 AND 720),   -- empty = use the sleep need from scoring
  pace_min_per_step INT NOT NULL DEFAULT 20 CHECK (pace_min_per_step BETWEEN 5 AND 60),
  step_days INT NOT NULL DEFAULT 3 CHECK (step_days BETWEEN 1 AND 14),
  start_date DATE NOT NULL,                 -- the plan starts on this night (sleep day = the evening before falling asleep)
  start_bedtime_off INT NOT NULL,           -- starting point: average bedtime of the last 7 nights when the goal was set (minutes relative to midnight, 06:30 = 390)
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
