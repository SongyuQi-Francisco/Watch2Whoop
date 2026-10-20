-- Grocery: product library, pantry, weekly shopping lists (docs/grocery-plan.md). Every table carries user_id.

-- Product library: structured by the user's AI from label / receipt / price tag photos.
CREATE TABLE grocery_products (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
  brand TEXT CHECK (brand IS NULL OR char_length(brand) <= 40),
  store TEXT CHECK (store IS NULL OR char_length(store) <= 40),            -- free text (Costco, Woolworths, a market...)
  category TEXT NOT NULL DEFAULT 'other',                                 -- grocery/params.CATEGORIES
  package_size NUMERIC CHECK (package_size IS NULL OR package_size > 0),
  package_unit TEXT CHECK (package_unit IN ('g', 'ml', 'pcs')),
  piece_g NUMERIC CHECK (piece_g IS NULL OR piece_g > 0),                 -- grams per piece for things sold by count (eggs)
  price NUMERIC CHECK (price IS NULL OR price >= 0),                      -- per package
  currency TEXT CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
  price_on DATE,
  nutrition JSONB NOT NULL DEFAULT '{}',   -- per 100 g (or 100 ml): kcal, protein_g, carbs_g, fat_g, sat_fat_g, sugar_g, sodium_mg; only what is known
  purine_level TEXT CHECK (purine_level IN ('low', 'medium', 'high')),
  red_meat BOOLEAN NOT NULL DEFAULT FALSE,                                -- red or processed meat (counts toward the weekly cap)
  barcode TEXT CHECK (barcode IS NULL OR barcode ~ '^[0-9]{6,14}$'),
  matches TEXT[] NOT NULL DEFAULT '{}',    -- ingredient names this product covers (recipe / template food names)
  note TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX grocery_products_name_uq ON grocery_products (user_id, lower(name), lower(coalesce(brand, ''))) WHERE active;
CREATE UNIQUE INDEX grocery_products_barcode_uq ON grocery_products (user_id, barcode) WHERE active AND barcode IS NOT NULL;

-- Pantry: rough quantities at home.
CREATE TABLE pantry_items (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
  product_id INT REFERENCES grocery_products(id) ON DELETE SET NULL,
  quantity NUMERIC NOT NULL CHECK (quantity >= 0),
  unit TEXT NOT NULL CHECK (unit IN ('g', 'ml', 'pcs')),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX pantry_items_name_uq ON pantry_items (user_id, lower(name));

-- One shopping list per user per week (week_start = Monday in the user's time zone).
CREATE TABLE shopping_lists (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start DATE NOT NULL CHECK (extract(isodow FROM week_start) = 1),
  plan JSONB NOT NULL DEFAULT '[]',        -- what the list was generated from: [{kind, ref_id, food, amount, unit}]
  summary JSONB NOT NULL DEFAULT '{}',     -- generation-time totals for health hints: red_meat_g, short_rib
  generated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, week_start)
);

CREATE TABLE shopping_items (
  id BIGSERIAL PRIMARY KEY,
  list_id INT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
  product_id INT REFERENCES grocery_products(id) ON DELETE SET NULL,
  category TEXT NOT NULL DEFAULT 'other',
  store TEXT,
  need_qty NUMERIC CHECK (need_qty IS NULL OR need_qty >= 0),   -- what the plan needs after the pantry, in unit
  unit TEXT NOT NULL CHECK (unit IN ('g', 'ml', 'pcs')),
  packages INT CHECK (packages IS NULL OR packages >= 1),
  buy_qty NUMERIC NOT NULL CHECK (buy_qty > 0),                 -- what to buy, in unit (packages x size, or rounded up)
  est_cost NUMERIC,
  currency TEXT,
  source TEXT NOT NULL CHECK (source IN ('plan', 'manual')),
  used_by TEXT[] NOT NULL DEFAULT '{}',                         -- recipe / template names
  note TEXT,
  bought_at TIMESTAMPTZ,
  paid_price NUMERIC CHECK (paid_price IS NULL OR paid_price >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX shopping_items_list_idx ON shopping_items (list_id);
CREATE INDEX shopping_items_user_idx ON shopping_items (user_id);
