"""Own storage for food, food library, weight and workout logs, replacing SparkyFitness (issue #252,
docs/own-food-weight-storage.md). Phase 2: mapping.py (SparkyFitness rows -> table rows), store.py (upsert / delete per
user and date range), mirror.py (dual write behind the rollout flag own_food_store), migrate.py (`body-engine
sparky-migrate`: copy and check per table). Nothing reads these tables yet; the read switch is phase 3.
"""
