-- Grocery: which supermarkets a user has connected (issue #44). None by default: store links (and later store prices) are shown
-- only for connected chains. Keys are grocery/params.STORES keys ('coles', 'woolworths'); validated in grocery/store.py.
CREATE TABLE grocery_settings (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  stores TEXT[] NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
