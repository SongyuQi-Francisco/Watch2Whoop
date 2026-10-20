-- Grocery: real store links (docs/grocery-plan.md §7). Only Coles / Woolworths product pages are accepted; the server never fetches them.
-- product_url is stored canonical (no query / fragment); store_sku is parsed from it (Coles product id, Woolworths stockcode).
-- nutrition_source records where per-100 g values came from when not from the user's own label photo (e.g. "Open Food Facts (ODbL)").
ALTER TABLE grocery_products
  ADD COLUMN product_url TEXT CHECK (product_url IS NULL OR (char_length(product_url) <= 300 AND product_url ~ '^https://www\.(coles|woolworths)\.com\.au/')),
  ADD COLUMN store_sku TEXT CHECK (store_sku IS NULL OR store_sku ~ '^[0-9]{1,12}$'),
  ADD COLUMN nutrition_source TEXT CHECK (nutrition_source IS NULL OR char_length(nutrition_source) <= 60),
  ADD CONSTRAINT grocery_products_url_sku CHECK ((product_url IS NULL) = (store_sku IS NULL));
