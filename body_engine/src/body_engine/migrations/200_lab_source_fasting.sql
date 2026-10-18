-- Lab results: where a result came from and whether it was fasting (lab trend page, docs/system-design.md §7).
-- source: lab = a pathology lab / clinic report; home_device = a home meter (e.g. a uric acid meter, larger error, shown as hollow points).
-- fasting: true = fasting, false = after eating, NULL = unknown. Home uric acid after a meal reads higher, so it is a field of its own
-- instead of free text in note.
ALTER TABLE lab_results
  ADD COLUMN source TEXT NOT NULL DEFAULT 'lab',
  ADD COLUMN fasting BOOLEAN,
  ADD CONSTRAINT lab_results_source_check CHECK (source IN ('lab', 'home_device'));

-- Backfill from what was written before these fields existed: lab name / note mentioning a home meter -> home_device;
-- a note that clearly says after a meal / fasting -> fasting. Text that cannot be read is left as lab / NULL.
-- The "化验单原名：..." part the butler appends is ignored (a test called 空腹血糖 says nothing about this reading).
UPDATE lab_results SET source = 'home_device'
 WHERE coalesce(lab, '') ~* '(尿酸仪|血糖仪|血脂仪|家用|家测|自测|在家|试纸|home|meter|device|benecheck|accutrend|cardiochek)'
    OR regexp_replace(coalesce(note, ''), '化验单原名：[^;；]*', '', 'g')
       ~* '(尿酸仪|血糖仪|血脂仪|家用仪器|家用设备|家测|自测|在家测|指尖血|试纸|home meter|home device)';

WITH n AS (
  SELECT id, regexp_replace(coalesce(note, ''), '化验单原名：[^;；]*', '', 'g') AS t FROM lab_results
)
UPDATE lab_results l SET fasting = CASE
    WHEN n.t ~ '(非空腹|不是空腹|没空腹|未空腹|饭后|餐后|进食后|吃完|吃过饭|吃饭后)' THEN false
    WHEN n.t ~ '空腹' THEN true
  END
  FROM n
 WHERE n.id = l.id AND l.fasting IS NULL AND n.t ~ '(空腹|饭后|餐后|进食后|吃完|吃过饭|吃饭后)';
