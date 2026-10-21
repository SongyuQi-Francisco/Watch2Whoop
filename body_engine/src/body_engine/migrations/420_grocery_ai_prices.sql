-- Grocery: prices looked up by the user's own AI (docs/grocery-plan.md §9). The server never visits store websites; the user's AI
-- reads the pages it is allowed to read and writes what it saw back through record_store_prices. We only keep structure and storage.
--
-- 1. store_products becomes the de-duplicated product pool: one row per physical product per store. Identity, in this order:
--    store + the store's product id (sku, parsed from the Coles / WWS product URL), else store + barcode, else store + normalised
--    name and size (name_key). Exception to "every table carries user_id" (like migration 320): a pool row is product metadata only
--    (name, brand, size, barcode, product page, image URL reference). It carries no price for user-added rows and no personal
--    data; who can see a row is decided by user_store_products below. Prices are per user (price_observations.user_id).
--    The image is never downloaded: image_url is a reference to the store's own image (the Today page's CSP does not load it).
ALTER TABLE store_products ALTER COLUMN sku DROP NOT NULL;
ALTER TABLE store_products ALTER COLUMN url DROP NOT NULL;
ALTER TABLE store_products ALTER COLUMN first_seen SET DEFAULT current_date;
ALTER TABLE store_products ALTER COLUMN last_seen SET DEFAULT current_date;
ALTER TABLE store_products
  ADD COLUMN origin TEXT NOT NULL DEFAULT 'sync' CHECK (origin IN ('sync', 'user')),   -- sync = grocery-sync catalogue (#48)
  ADD COLUMN name_key TEXT CHECK (name_key IS NULL OR char_length(name_key) <= 200),
  ADD COLUMN image_url TEXT CHECK (image_url IS NULL OR (image_url ~ '^https://' AND char_length(image_url) <= 500)),
  ADD CONSTRAINT store_products_identity CHECK (sku IS NOT NULL OR barcode IS NOT NULL OR name_key IS NOT NULL);
CREATE UNIQUE INDEX store_products_barcode_uq ON store_products (store, barcode) WHERE sku IS NULL AND barcode IS NOT NULL;
CREATE UNIQUE INDEX store_products_name_uq ON store_products (store, name_key) WHERE sku IS NULL AND barcode IS NULL;

-- 2. Visibility: a user sees a pool product only after they uploaded or linked it themselves. Someone else's product is invisible,
--    also by guessing its id (every read joins this table; a missing link answers exactly like a missing product).
CREATE TABLE user_store_products (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  store_product_id INT NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
  matches TEXT[] NOT NULL DEFAULT '{}',          -- this user's ingredient / list names the product covers (鸡胸肉, chicken breast)
  source TEXT NOT NULL CHECK (source IN ('ai', 'screenshot', 'manual', 'list')),
  linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, store_product_id)
);
CREATE INDEX user_store_products_product_idx ON user_store_products (store_product_id);

-- 3. Price observations are per user and never expire (the page shows how old they are). Rows from grocery-sync keep user_id NULL.
ALTER TABLE price_observations DROP CONSTRAINT price_observations_store_product_id_observed_on_key;
ALTER TABLE price_observations
  ADD COLUMN user_id INT REFERENCES users(id) ON DELETE CASCADE,
  ADD COLUMN observed_at TIMESTAMPTZ,
  ADD COLUMN source TEXT CHECK (source IN ('sync', 'ai', 'screenshot', 'manual')),
  ADD COLUMN unit_price_unit TEXT CHECK (unit_price_unit IN ('kg', 'L', 'ea')),
  ADD COLUMN special_label TEXT CHECK (special_label IS NULL OR char_length(special_label) <= 60),
  ADD COLUMN currency TEXT NOT NULL DEFAULT 'AUD' CHECK (currency ~ '^[A-Z]{3}$'),
  ADD CONSTRAINT price_observations_price_ok CHECK (price IS NULL OR price >= 0),
  ADD CONSTRAINT price_observations_user_source CHECK ((user_id IS NULL) = (source IS NULL OR source = 'sync'));
UPDATE price_observations SET source = 'sync', observed_at = observed_on::timestamp AT TIME ZONE 'Australia/Sydney'
 WHERE source IS NULL;
ALTER TABLE price_observations ALTER COLUMN observed_at SET NOT NULL;
CREATE UNIQUE INDEX price_observations_sync_uq ON price_observations (store_product_id, observed_on) WHERE user_id IS NULL;
-- the same reading sent twice (an AI retrying) is stored once
CREATE UNIQUE INDEX price_observations_user_uq ON price_observations (user_id, store_product_id, observed_at, source)
  WHERE user_id IS NOT NULL;
CREATE INDEX price_observations_user_idx ON price_observations (user_id, store_product_id, observed_at DESC) WHERE user_id IS NOT NULL;

-- 4. Purchase history: what the user actually paid, where, when, on special or not (ticking a list line with a price, or the AI
--    recording a purchase). Unticking removes the line's purchase.
CREATE TABLE grocery_purchases (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  store_product_id INT REFERENCES store_products(id) ON DELETE SET NULL,
  product_id INT REFERENCES grocery_products(id) ON DELETE SET NULL,
  shopping_item_id BIGINT UNIQUE REFERENCES shopping_items(id) ON DELETE SET NULL,
  name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 160),
  store TEXT CHECK (store IS NULL OR char_length(store) <= 40),
  bought_on DATE NOT NULL,
  packages NUMERIC CHECK (packages IS NULL OR packages > 0),
  paid_total NUMERIC CHECK (paid_total IS NULL OR paid_total >= 0),
  unit_paid NUMERIC CHECK (unit_paid IS NULL OR unit_paid >= 0),         -- per package
  paid_estimated BOOLEAN NOT NULL DEFAULT FALSE,                         -- ticked without a price: the recorded store price
  on_special BOOLEAN NOT NULL DEFAULT FALSE,
  was_price NUMERIC CHECK (was_price IS NULL OR was_price >= 0),
  currency TEXT NOT NULL DEFAULT 'AUD' CHECK (currency ~ '^[A-Z]{3}$'),
  source TEXT NOT NULL CHECK (source IN ('list', 'ai', 'manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX grocery_purchases_user_idx ON grocery_purchases (user_id, bought_on DESC);
CREATE INDEX grocery_purchases_product_idx ON grocery_purchases (user_id, store_product_id);

-- 5. In-page chat (API keys): the official web search / page reading tools of Anthropic, OpenAI and Gemini, off by default.
--    Searches are billed on the user's own key. The fallback AI never gets them.
ALTER TABLE ai_providers ADD COLUMN web_tools BOOLEAN NOT NULL DEFAULT FALSE;
