-- Usual items ("常买", issue #103, docs/grocery-plan.md §3): products the user confirmed they buy on a fixed rhythm (eggs, milk,
-- bananas). generate_shopping_list adds the ones that fall due in the list's week (interval since the last purchase in
-- grocery_purchases) unless the pantry already holds one purchase's worth, as source 'staple'. Suggestions (bought often lately)
-- are computed from grocery_purchases and only marked after the user confirms (set_staple); nothing is marked automatically.
-- grocery_products already carries user_id; every read / write filters on it.
ALTER TABLE grocery_products
  ADD COLUMN staple BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN staple_every_days INT CHECK (staple_every_days IS NULL OR staple_every_days BETWEEN 1 AND 180),
  ADD COLUMN staple_qty NUMERIC CHECK (staple_qty IS NULL OR staple_qty > 0),        -- how much each time, in staple_unit
  ADD COLUMN staple_unit TEXT CHECK (staple_unit IS NULL OR staple_unit IN ('pack', 'g', 'ml', 'pcs'));   -- pack = packages
CREATE INDEX grocery_products_staple_idx ON grocery_products (user_id) WHERE staple AND active;

ALTER TABLE shopping_items DROP CONSTRAINT shopping_items_source_check;
ALTER TABLE shopping_items ADD CONSTRAINT shopping_items_source_check CHECK (source IN ('plan', 'manual', 'staple'));
