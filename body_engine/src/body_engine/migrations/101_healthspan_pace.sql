-- Pace of aging changed to a 26-week window + 4-week moving average (decided 2026-10-17 by the maintainer, hs-v1.1): records how many weeks were actually used.
ALTER TABLE healthspan_weekly ADD COLUMN pace_window_weeks INT;   -- number of weekly points used in the regression (26 when full; fewer than 26 early on uses all of them, needs >= 8 to produce a value)
COMMENT ON COLUMN healthspan_weekly.pace_of_aging IS 'Years the body ages per calendar year = 1 + dΔ/dt (Δ first smoothed with a 4-week moving average, regressed over the last 26 weeks), clipped to -1 to 3';
COMMENT ON COLUMN healthspan_weekly.pace_raw IS 'Same as above, unclipped';
