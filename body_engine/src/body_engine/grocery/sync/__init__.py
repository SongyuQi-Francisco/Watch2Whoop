"""grocery-sync: the opt-in weekly refresh of the supermarkets' public food catalogue (docs/grocery-store-data-plan.md §8).

Off unless the deployer sets BODY_GROCERY_SYNC (stores) and BODY_GROCERY_SYNC_CONTACT (goes into the User-Agent).
- robots.py: RFC 9309 robots.txt matching; http.py: the polite client (robots per URL, delay, backoff, stop on block).
- coles.py / wws.py: one isolated fetcher + parser per store. Tests use fixtures; CI never reaches the live sites.
- runner.py: run / status / purge (CLI `body-engine grocery-sync`); offers.py: the read side (connected stores only)."""
