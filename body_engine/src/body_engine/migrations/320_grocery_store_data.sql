-- Grocery store data (docs/grocery-store-data-plan.md §8): the supermarkets' public product catalogue, refreshed weekly by the
-- opt-in grocery-sync component (off by default). Exception to "every table carries user_id": these rows are the stores' public
-- catalogue, not anyone's personal data; one copy is shared by all users, and who sees what is decided at read time
-- (the component switch + each user's connected stores, grocery_settings). `grocery-sync purge` deletes all of it.
-- No images, no long descriptions: only the fields below.
CREATE TABLE store_products (
  id SERIAL PRIMARY KEY,
  store TEXT NOT NULL CHECK (store ~ '^[a-z]{2,20}$'),           -- grocery/params.STORES key
  sku TEXT NOT NULL CHECK (sku ~ '^[0-9]{1,12}$'),
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 160),
  brand TEXT CHECK (brand IS NULL OR char_length(brand) <= 60),
  size_text TEXT CHECK (size_text IS NULL OR char_length(size_text) <= 40),
  package_size NUMERIC CHECK (package_size IS NULL OR package_size > 0),
  package_unit TEXT CHECK (package_unit IN ('g', 'ml', 'pcs')),
  barcode TEXT CHECK (barcode IS NULL OR barcode ~ '^[0-9]{6,14}$'),
  category TEXT,                                                  -- the store's top-level category slug
  url TEXT NOT NULL CHECK (url ~ '^https://www\.(coles|woolworths)\.com\.au/'),
  price NUMERIC CHECK (price IS NULL OR price >= 0),
  was_price NUMERIC CHECK (was_price IS NULL OR was_price >= 0),
  unit_price NUMERIC CHECK (unit_price IS NULL OR unit_price >= 0),
  unit_price_unit TEXT CHECK (unit_price_unit IN ('kg', 'L', 'ea')),
  special_type TEXT CHECK (special_type IN ('half_price', 'price_drop', 'multibuy', 'special')),
  special_label TEXT CHECK (special_label IS NULL OR char_length(special_label) <= 60),
  save_amount NUMERIC,
  multibuy_qty INT,
  multibuy_unit_price NUMERIC,
  first_seen DATE NOT NULL,
  last_seen DATE NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,                           -- seen in the latest complete run
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (store, sku)
);
CREATE INDEX store_products_special_idx ON store_products (store, special_type) WHERE active;

-- One row per run per store (grocery-sync status, the doctor).
CREATE TABLE grocery_sync_runs (
  id SERIAL PRIMARY KEY,
  store TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'blocked', 'unclear', 'failed')),
  requests INT NOT NULL DEFAULT 0,
  pages INT NOT NULL DEFAULT 0,
  products INT NOT NULL DEFAULT 0,
  specials INT NOT NULL DEFAULT 0,
  half_price INT NOT NULL DEFAULT 0,
  error TEXT
);
CREATE INDEX grocery_sync_runs_store_idx ON grocery_sync_runs (store, started_at DESC);

-- Price history: one row per product per day it was observed (re-running the same day overwrites: idempotent).
CREATE TABLE price_observations (
  id BIGSERIAL PRIMARY KEY,
  store_product_id INT NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
  observed_on DATE NOT NULL,                                      -- Australia/Sydney date
  price NUMERIC,
  was_price NUMERIC,
  unit_price NUMERIC,
  special_type TEXT,
  run_id INT REFERENCES grocery_sync_runs(id) ON DELETE SET NULL,
  UNIQUE (store_product_id, observed_on)
);

-- This week's specials per store (the specials week starts on Wednesday, Australia/Sydney).
CREATE TABLE weekly_specials (
  id BIGSERIAL PRIMARY KEY,
  store TEXT NOT NULL,
  week_start DATE NOT NULL CHECK (extract(isodow FROM week_start) = 3),
  store_product_id INT NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
  special_type TEXT NOT NULL CHECK (special_type IN ('half_price', 'price_drop', 'multibuy', 'special')),
  price NUMERIC,
  was_price NUMERIC,
  save_amount NUMERIC,
  label TEXT,
  valid_to DATE NOT NULL,
  UNIQUE (store, week_start, store_product_id)
);
CREATE INDEX weekly_specials_week_idx ON weekly_specials (store, week_start, special_type);
