-- Today page pantry view (issue #102): remember how much of a planned line the pantry covered when the list was generated, so the
-- list can say why less is bought (「家里有 6 个」). In the line's unit; NULL = the pantry did not cover any of it (or the line is
-- older than this column / manual). Fully covered needs have no line at all (generate returns them as covered_by_pantry).
-- IF NOT EXISTS: development databases that ran this file under its first name (490_shopping_pantry_used.sql) apply it again.
ALTER TABLE shopping_items ADD COLUMN IF NOT EXISTS pantry_qty NUMERIC CHECK (pantry_qty IS NULL OR pantry_qty > 0);
