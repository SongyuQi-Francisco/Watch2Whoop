"""grocery-sync parameters (docs/grocery-store-data-plan.md §8). Crawl politeness, store categories, matching and health filters.

Health filters reuse the rules (rules/params.py red-meat keywords and caps; grocery/params.py FSA lines need nutrition, which the
store pages do not give, so specials are filtered by name / category only and flagged as estimates)."""
import os

# ---------------------------------------------------------------- switch (deployer opt-in, off by default)
ENV_STORES = "BODY_GROCERY_SYNC"            # e.g. "coles" or "coles,woolworths"; unset / empty = off
ENV_CONTACT = "BODY_GROCERY_SYNC_CONTACT"   # e-mail or URL of the deployer; required (User-Agent)
ENV_DELAY = "BODY_GROCERY_SYNC_DELAY_S"
ENV_MAX_REQUESTS = "BODY_GROCERY_SYNC_MAX_REQUESTS"
SYNC_STORES = ("coles", "woolworths")      # stores that have a fetcher module

# ---------------------------------------------------------------- politeness
DELAY_S = 6.0            # between requests (plus JITTER_S random), single thread
DELAY_MIN_S = 3.0        # the env var cannot go lower
JITTER_S = 2.0
TIMEOUT_S = 20
RETRIES = 3              # timeouts / 5xx only; 403 / 429 stop the store at once
BACKOFF_S = (30, 60, 120)
MAX_REQUESTS = 800       # per store per run
MIN_DAYS_BETWEEN_RUNS = 6   # `run` skips when an ok run is younger (the timer may fire twice); --force overrides
STALE_DAYS = 8           # status / doctor warn when the last ok run is older
TZ = "Australia/Sydney"  # specials weeks start on Wednesday here
SPECIALS_WEEKDAY = 2     # Monday = 0 -> Wednesday

# ---------------------------------------------------------------- Coles (robots.txt checked 2026-10-21: /browse/ allowed)
COLES_BASE = "https://www.coles.com.au"
COLES_BROWSE = COLES_BASE + "/browse/{cat}"          # + ?page=N from page 2
COLES_PRODUCT = COLES_BASE + "/product/{slug}-{id}"
COLES_PAGE_SIZE = 48
# Food top-level categories only (no health & beauty, household, pet, baby, liquor, tobacco)
COLES_CATEGORIES = ("meat-seafood", "fruit-vegetables", "dairy-eggs-fridge", "bakery", "deli", "pantry", "frozen", "drinks",
                    "chips-chocolates-snacks", "health-dietary", "international-foods")

# ---------------------------------------------------------------- Woolworths (WWS)
# 2026-10-21: Akamai answers 403 to an identifiable User-Agent, already for robots.txt -> not crawled (§8.4). Only the robots
# check runs; there is no parser until access is actually allowed.
WWS_BASE = "https://www.woolworths.com.au"

# ---------------------------------------------------------------- matching (offers.py)
# Common ingredient names -> English search terms (the stores only understand English). First match wins (longer keys first).
ZH_EN: list[tuple[str, str]] = [
    ("鸡胸肉", "chicken breast"), ("鸡胸", "chicken breast"), ("鸡腿肉", "chicken thigh"), ("鸡腿", "chicken thigh"),
    ("鸡翅", "chicken wings"), ("鸡肉末", "chicken mince"), ("全鸡", "whole chicken"), ("三文鱼", "salmon"),
    ("金枪鱼", "tuna"), ("鳕鱼", "cod"), ("巴沙鱼", "basa"), ("虾仁", "prawns"), ("虾", "prawns"), ("鸡蛋", "eggs"),
    ("蛋", "eggs"), ("希腊酸奶", "greek yoghurt"), ("酸奶", "yoghurt"), ("脱脂牛奶", "skim milk"), ("牛奶", "milk"),
    ("奶酪", "cheese"), ("芝士", "cheese"), ("茅屋奶酪", "cottage cheese"), ("豆腐", "tofu"), ("燕麦", "oats"),
    ("糙米", "brown rice"), ("米饭", "rice"), ("大米", "rice"), ("意面", "pasta"), ("全麦面包", "wholemeal bread"),
    ("面包", "bread"), ("土豆", "potato"), ("红薯", "sweet potato"), ("西兰花", "broccoli"), ("菠菜", "spinach"),
    ("生菜", "lettuce"), ("番茄", "tomato"), ("西红柿", "tomato"), ("黄瓜", "cucumber"), ("胡萝卜", "carrot"),
    ("洋葱", "onion"), ("蘑菇", "mushroom"), ("青椒", "capsicum"), ("彩椒", "capsicum"), ("南瓜", "pumpkin"),
    ("西葫芦", "zucchini"), ("卷心菜", "cabbage"), ("白菜", "wombok"), ("牛油果", "avocado"), ("香蕉", "banana"),
    ("苹果", "apple"), ("蓝莓", "blueberries"), ("草莓", "strawberries"), ("橙子", "orange"), ("柠檬", "lemon"),
    ("牛肉末", "beef mince"), ("牛肉馅", "beef mince"), ("瘦牛肉", "lean beef"), ("牛排", "beef steak"),
    ("猪里脊", "pork fillet"), ("猪肉末", "pork mince"), ("羊排", "lamb cutlets"), ("橄榄油", "olive oil"),
    ("花生酱", "peanut butter"), ("杏仁", "almonds"), ("毛豆", "edamame"), ("鹰嘴豆", "chickpeas"),
]

# ---------------------------------------------------------------- recommendations (weekly specials)
SPECIALS_LIMIT = 20
# Never recommended as a special, for anybody (generic public-health guidance, docs/grocery-store-data-plan.md §8.6):
# foods high in fat / sugar / salt (UK DHSC 2021 HFSS rules; WHO 2015 free sugars), processed meat (IARC 2015 group 1;
# WCRF/AICR 2018 "little if any") and alcohol (WHO Europe 2023: no safe level).
EXCLUDE_CATEGORIES = ("chips-chocolates-snacks",)
EXCLUDE_KEYWORDS = ("ice cream", "chocolate", "lolly", "lollies", "biscuit", "cookie", "cake", "donut", "doughnut", "chips",
                    "crisps", "soft drink", "cola", "lemonade", "energy drink", "cordial", "juice", "syrup", "dessert",
                    "brownie", "muffin", "croissant", "pie", "sausage roll", "pizza", "nugget", "schnitzel", "battered",
                    "crumbed", "fried", "bacon", "salami", "chorizo", "kabana", "frankfurt", "hot dog", "beer", "wine",
                    "pastry")
# Only for a user with the low_saturated_fat diet constraint (nutrition/constraints.py, issue #76)
EXCLUDE_IF_LOW_SAT_FAT = ("cream", "butter")
EXCLUDE_UNLESS = ("peanut butter", "almond butter")   # whole-word matches above that are fine after all
# High-purine foods (flagged; ranked lower only for a user with the low_purine diet constraint): offal, small oily fish,
# some shellfish, yeast extracts.
HIGH_PURINE_KEYWORDS = ("liver", "kidney", "heart", "anchov", "sardine", "mackerel", "herring", "mussel", "scallop",
                        "trout", "vegemite", "yeast extract")
# Ranking of half-price items by category (lower = earlier): lean protein and produce first.
CATEGORY_RANK = {"meat-seafood": 0, "fruit-vegetables": 0, "dairy-eggs-fridge": 1, "deli": 2, "bakery": 2, "pantry": 2,
                 "frozen": 2, "health-dietary": 2, "international-foods": 3, "drinks": 3}
# Meal suggestions: score bonus per recipe ingredient that is half price this week (recipe_params scale), capped.
BONUS_HALF_PRICE = 0.15
BONUS_HALF_PRICE_MAX = 0.3


def enabled_stores() -> list[str]:
    """Stores the deployer turned on (BODY_GROCERY_SYNC) that have a fetcher; [] = the component is off."""
    raw = os.environ.get(ENV_STORES) or ""
    want = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return [s for s in SYNC_STORES if s in want]


def contact() -> str | None:
    return (os.environ.get(ENV_CONTACT) or "").strip() or None


def delay_s() -> float:
    try:
        return max(DELAY_MIN_S, float(os.environ.get(ENV_DELAY) or DELAY_S))
    except ValueError:
        return DELAY_S


def max_requests() -> int:
    try:
        return max(1, int(os.environ.get(ENV_MAX_REQUESTS) or MAX_REQUESTS))
    except ValueError:
        return MAX_REQUESTS
