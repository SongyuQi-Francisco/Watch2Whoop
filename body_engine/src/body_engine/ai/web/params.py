"""Parameters of the server-side web tools (ai/web/, issue #150). Change limits and prices here.

Search API prices (USD per query, pay-as-you-go after any free allowance), checked 2026-10-22:
  brave   https://api-dashboard.search.brave.com/documentation/pricing  Search: $5 / 1,000 requests, $5 of credit every month
          (≈ 1,000 free queries / month; a card is needed even for the free credit)
  tavily  https://www.tavily.com/pricing  basic search = 1 credit; 1,000 free credits / month; pay-as-you-go $0.008 / credit
  serper  https://serper.dev  Google results; 2,500 free queries once; prepaid packs from $50 / 50,000 ($1 / 1,000), valid 6 months
Every query is counted at the paid price (free credits ignored), converted at USD_CNY (rounded up), so what is charged against
the fallback quota is never lower than the real bill.
"""
import os

FLAG = "ai_web"                       # rollout flag (rollout/registry.py), off by default

# ---------------------------------------------------------------- search API
PROVIDER_ENV = "BODY_WEB_SEARCH_PROVIDER"            # brave (default) / tavily / serper
KEY_FILE_NAME = "web_search_api_key"                 # secrets/web_search_api_key, or BODY_WEB_SEARCH_API_KEY_FILE
DEFAULT_PROVIDER = "brave"
PRICE_USD = {"brave": 0.005, "tavily": 0.008, "serper": 0.001}
USD_CNY = 7.3                                        # conservative rate for the CNY quota (2026-10: about 7.1)
SEARCH_RESULTS = 5                                   # results returned to the model per query
SNIPPET_CHARS = 300
QUERY_MAX_CHARS = 200
SEARCH_TIMEOUT_S = 10
# Search results localised by the user's time zone (prefix match); others get the provider's default
TZ_COUNTRY = {"Australia/": "AU", "Pacific/Auckland": "NZ", "Asia/Shanghai": "CN", "Asia/Hong_Kong": "HK",
              "Asia/Singapore": "SG", "Asia/Kuala_Lumpur": "MY", "America/": "US", "Europe/London": "GB"}

# ---------------------------------------------------------------- limits (per person, local day; per deployment, month)
SEARCHES_PER_DAY = 20                 # one shopping list prices a dozen items: ~10–15 searches
FETCHES_PER_DAY = 40
SEARCHES_PER_MONTH_ALL = 3000         # all users together (calendar month, UTC): caps the admin's search bill (~$15 on brave)

# ---------------------------------------------------------------- page reading
ROBOTS_AGENT = "Watch2WhoopBot"       # the token matched against robots.txt User-agent lines


def user_agent() -> str:
    """Who we are, honestly (never a browser's string); the deployment's BODY_PUBLIC_URL as the contact address."""
    url = (os.environ.get("BODY_PUBLIC_URL") or "").strip().rstrip("/")
    return f"{ROBOTS_AGENT}/1.0 ({'+' + url + '; ' if url else ''}reads one page a user asked about, obeys robots.txt)"

FETCH_TIMEOUT_S = 10
MAX_PAGE_BYTES = 1_500_000
MAX_ROBOTS_BYTES = 500_000
MAX_REDIRECTS = 3
URL_MAX_CHARS = 500
TEXT_CHARS = 4000                     # page text excerpt given to the model (never stored in the product database)
MAX_PRODUCTS = 10                     # schema.org Product entries read from one page
ROBOTS_CACHE_S = 3600
BLOCKED_HOST_CACHE_S = 3600           # a host that blocked us is not asked again for this long (no retry loops)
CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
# Sites known to sit behind bot protection: never fetched (the AI asks for a screenshot or receipt instead). We do not try
# our luck against them: a challenge page would only be another thing not to get around.
NO_FETCH_HOSTS = ("coles.com.au", "woolworths.com.au")
# HTTP statuses that mean "you may not": stop, never retry
BLOCK_STATUSES = (401, 403, 407, 429, 451, 503)
# Lower-case markers of bot challenges / CAPTCHAs in the first part of a page
CHALLENGE_MARKERS = ("captcha", "cf-chl", "challenge-platform", "just a moment...", "attention required! | cloudflare",
                     "_incapsula_resource", "incapsula incident", "px-captcha", "perimeterx", "datadome",
                     "pardon our interruption", "are you a robot", "verify you are a human", "verify you are human",
                     "access denied", "request unsuccessful", "unusual traffic")
CHALLENGE_SCAN_CHARS = 30000
LOGIN_TEXT_MAX = 1500                 # a password field on a page with less text than this = login wall
