"""Own storage for food, food library, weight and workout logs, replacing SparkyFitness (issue #252,
docs/own-food-weight-storage.md). Phase 2: mapping.py (SparkyFitness rows -> table rows), store.py (upsert / delete per
user and date range), mirror.py (dual write behind the rollout flag own_food_store), migrate.py (`body-engine
sparky-migrate`: copy and check per table). Nothing reads these tables yet; the read switch is phase 3.

Licence (docs/own-food-weight-storage.md §12): this is an independent implementation. SparkyFitness's licence keeps
modified versions and works containing substantial parts of it non-commercial, so nothing here is copied or adapted from
its source, SQL schema or migrations; tables, code and field names are Body Engine's own. The only contact is its public
HTTP API, read with each user's own key to fetch that user's own data.
"""
