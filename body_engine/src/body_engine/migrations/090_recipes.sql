-- Recipe management (docs/system-design.md §15). Recipe "storage" keeps using SparkyFitness meals (the 25 imported during migration),
-- Body Engine adds metadata: which meal it suits, tags, time, servings, method, ingredients (with nutrition, purchase unit, store), favorite, can-make, health-constraint flags.
-- Nutrition follows the ingredients here (a SparkyFitness meal has only calories and protein; it is a mirror).
CREATE TABLE recipe_meta (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sparky_meal_id TEXT,                     -- SparkyFitness meals.id (mirror; created together when a recipe is created)
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
  cuisine TEXT,
  slots TEXT[] NOT NULL DEFAULT '{}',      -- breakfast / lunch / dinner / snack, multiple allowed
  tags TEXT[] NOT NULL DEFAULT '{}',       -- manual tags (auto tags are added on read from nutrition and constraints)
  prep_min INT CHECK (prep_min IS NULL OR prep_min BETWEEN 0 AND 600),
  cook_min INT CHECK (cook_min IS NULL OR cook_min BETWEEN 0 AND 600),
  servings NUMERIC NOT NULL DEFAULT 1 CHECK (servings > 0),   -- ingredients are recorded for the whole pot; how many servings in total
  serving_label TEXT,                      -- what "1 serving" means, e.g. 1 serving (excluding staple)
  includes_staple BOOLEAN NOT NULL DEFAULT FALSE,             -- each serving already includes the staple (rice / noodles / oats...)
  ingredients JSONB NOT NULL DEFAULT '[]', -- [{food, grams, kcal, protein_g, carbs_g, fat_g, sat_fat_g, purine_mg,
                                           --   red_meat_g, purchase_unit, store, note}], amounts for the whole pot
  steps TEXT[] NOT NULL DEFAULT '{}',
  note TEXT,
  image TEXT,                              -- image URL, to be added later
  favorite BOOLEAN NOT NULL DEFAULT FALSE,
  can_make BOOLEAN NOT NULL DEFAULT FALSE, -- can cook it now / able to make
  last_cooked_on DATE,                     -- date of the most recent logged meal
  constraint_flags JSONB,                  -- per serving: purine, saturated fat, red meat (computed by recipes.constraint_flags, refreshed on write)
  source TEXT NOT NULL DEFAULT 'seed' CHECK (source IN ('seed', 'assistant', 'user')),
  import_key TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, sparky_meal_id),
  UNIQUE (user_id, import_key)
);
CREATE UNIQUE INDEX recipe_meta_name_uq ON recipe_meta (user_id, name) WHERE active;

-- Recipe logs also use meal_logs (sharing the undo endpoint with meal templates)
ALTER TABLE meal_logs ADD COLUMN recipe_id INT REFERENCES recipe_meta(id) ON DELETE SET NULL;
