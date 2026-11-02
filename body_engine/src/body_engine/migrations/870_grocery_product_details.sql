-- Grocery: fuller product archive (docs/grocery-plan.md §10, issue #291 / user feedback FB-16). Columns only, all nullable (or empty by
-- default); existing products, meal logging, the pantry and product cards keep working unchanged.
--   image_url        a reference to a picture the user's AI saw (https only). The server never fetches or re-hosts it, and the Today
--                    page's CSP does not load it; the page shows it as a plain link.
--   ingredients      the label's ingredient list as text
--   allergens        "contains" tags from the label; NULL = not recorded, '{}' = recorded as none
--   allergen_traces  "may contain / may be present" tags; same NULL / '{}' meaning
--   serving_size + serving_unit + nutrition_serving   the label's per-serving column, stored next to the per-100 g one.
-- nutrition (per 100 g / ml) and nutrition_serving are JSONB; the keys they may hold are grocery/params.NUTRITION_KEYS, which grows
-- from 7 to 14 (trans fat, added sugar, fibre, potassium, calcium, iron, cholesterol): no column change is needed for that.
ALTER TABLE grocery_products
  ADD COLUMN image_url TEXT CHECK (image_url IS NULL OR (image_url ~ '^https://' AND char_length(image_url) <= 500)),
  ADD COLUMN ingredients TEXT CHECK (ingredients IS NULL OR char_length(ingredients) <= 2000),
  ADD COLUMN allergens TEXT[],
  ADD COLUMN allergen_traces TEXT[],
  ADD COLUMN serving_size NUMERIC CHECK (serving_size IS NULL OR serving_size > 0),
  ADD COLUMN serving_unit TEXT CHECK (serving_unit IS NULL OR serving_unit IN ('g', 'ml')),
  ADD COLUMN nutrition_serving JSONB NOT NULL DEFAULT '{}',   -- per serving (serving_size serving_unit); only what is known
  ADD CONSTRAINT grocery_products_serving CHECK ((serving_size IS NULL) = (serving_unit IS NULL));
