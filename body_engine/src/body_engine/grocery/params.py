"""Grocery parameters: categories, rounding, and the "high" thresholds used to flag products (docs/grocery-plan.md).

Per-100 g thresholds follow the UK Food Standards Agency front-of-pack "traffic light" guidance (Guide to creating a front of pack
nutrition label, FSA / DH 2016): high = sat fat > 5 g, total sugars > 22.5 g, salt > 1.5 g (sodium ≈ salt / 2.5 -> 600 mg) per 100 g.
For drinks (per 100 ml) the FSA lines are halved: sat fat > 2.5 g, sugars > 11.25 g, salt > 0.75 g (sodium 300 mg).
Red meat / per-food weekly limits are the user's own diet constraints (nutrition/constraints.py, issue #76; none by default);
purine levels reuse the meal-template scale.
All are hints for the AI and the list view; nothing blocks a purchase."""

CATEGORIES = {   # key -> short Chinese label (UI order)
    "produce": "蔬果", "meat": "肉类", "seafood": "海鲜", "dairy_eggs": "蛋奶", "grains": "主食",
    "pantry": "调料干货", "frozen": "冷冻", "drinks": "饮品", "snacks": "零食", "supplements": "补剂",
    "household": "日用", "other": "其他",
}
UNITS = ("g", "ml", "pcs")
NUTRITION_KEYS = ("kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "sugar_g", "sodium_mg")

# Unmatched ingredients (no product in the library) are rounded up to this many grams / ml.
ROUND_G = 50

# FSA "high" per 100 g (solids) and per 100 ml (drinks)
HIGH_PER_100G = {"sat_fat_g": 5.0, "sugar_g": 22.5, "sodium_mg": 600}
HIGH_PER_100ML = {"sat_fat_g": 2.5, "sugar_g": 11.25, "sodium_mg": 300}
FLAG_LABELS = {"sat_fat_g": "饱和脂肪高", "sugar_g": "糖高", "sodium_mg": "钠高"}

# English twins for Food → Shopping in English (issue #42); the Chinese labels stay what the AI tools see
CATEGORIES_EN = {
    "produce": "Produce", "meat": "Meat", "seafood": "Seafood", "dairy_eggs": "Dairy & eggs", "grains": "Grains & bread",
    "pantry": "Pantry", "frozen": "Frozen", "drinks": "Drinks", "snacks": "Snacks", "supplements": "Supplements",
    "household": "Household", "other": "Other",
}
FLAGS_EN = {"饱和脂肪高": "High sat fat", "糖高": "High sugar", "钠高": "High sodium", "高嘌呤": "High purine",
            "红肉": "Red meat"}   # every product flag (FLAG_LABELS + store.product_flags)

# Keyword -> category guess for ingredients that have no product yet (first match wins; produce before meat so 牛油果 is not beef).
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("dairy_eggs", ("蛋", "奶", "酸奶", "芝士", "奶酪", "egg", "milk", "yogurt", "yoghurt", "cheese")),
    ("seafood", ("鱼", "虾", "蟹", "贝", "三文鱼", "金枪鱼", "鳕", "salmon", "tuna", "prawn", "shrimp", "fish")),
    ("grains", ("米", "面", "燕麦", "面包", "吐司", "意面", "粉", "rice", "oat", "bread", "pasta", "noodle", "wrap")),
    ("produce", ("菜", "瓜", "果", "葱", "姜", "蒜", "椒", "菇", "蘑菇", "番茄", "西红柿", "土豆", "红薯", "胡萝卜", "西兰花",
                 "牛油果", "香蕉", "苹果", "柠檬", "生菜", "菠菜", "avocado", "banana", "apple", "broccoli", "tomato",
                 "potato", "onion", "garlic", "spinach", "mushroom", "lettuce", "berry", "berries")),
    ("meat", ("鸡", "鸭", "牛", "猪", "羊", "肉", "培根", "火腿", "chicken", "beef", "pork", "lamb", "bacon", "ham", "mince")),
    ("pantry", ("油", "酱", "盐", "糖", "醋", "料酒", "粉丝", "豆", "坚果", "oil", "sauce", "salt", "sugar", "vinegar",
                "nut", "bean", "tofu")),
    ("drinks", ("咖啡", "茶", "汁", "水", "coffee", "tea", "juice")),
]

# Store links (docs/grocery-plan.md §7). Links only: the server never fetches, scrapes or embeds these sites (their terms forbid it).
# Product pages and Woolworths search open the store apps through universal links on iOS; Coles search opens in Safari.
# key -> label (short chip and display name), name (written to the store field), accepted product-page pattern (path; group 1 = SKU),
# search URL prefix, name aliases in the free-text store field. Links are shown only for the chains a user has connected (issue #44;
# none by default, table grocery_settings).
STORES = {
    "coles": {"label": "Coles", "name": "Coles", "host": "www.coles.com.au", "path": r"/product/[a-z0-9-]*?-?([0-9]{1,12})/?",
              "search": "https://www.coles.com.au/search/products?q=", "aliases": ("coles",)},
    "woolworths": {"label": "WWS", "name": "WWS", "host": "www.woolworths.com.au", "path": r"/shop/productdetails/([0-9]{1,12})(?:/[a-z0-9-]*)?/?",
                   "search": "https://www.woolworths.com.au/shop/search/products?searchTerm=",
                   "aliases": ("woolworths", "woolies", "woolworth", "wws")},
}
PRODUCT_URL_MAX = 300

# Open Food Facts (barcode -> nutrition per 100 g). ODbL: show the attribution next to the data. Single lookups only, never bulk.
# Their API asks for a custom User-Agent with contact info; read rate limit 100 req/min per IP (search 10/min) -> we stay far below.
# Off unless BODY_OFF_CONTACT (an e-mail or URL) is set.
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_PAGE_URL = "https://world.openfoodfacts.org/product/{code}"
OFF_FIELDS = "code,product_name,product_name_en,brands,quantity,nutriments"
OFF_PER_MINUTE = 15        # per process, all users together
OFF_PER_USER_DAY = 50
OFF_TIMEOUT_S = 8
OFF_ATTRIBUTION = "Open Food Facts (ODbL)"

# ---------------------------------------------------------------- prices from the user's own AI (docs/grocery-plan.md §9)
# The server never fetches store pages; the user's AI looks prices up and writes them with record_store_prices.
PRICE_SOURCES = ("ai", "screenshot", "manual")
SPECIAL_TYPES = ("half_price", "price_drop", "multibuy", "special")
RECORD_MAX_ITEMS = 30        # per call: the AI looks up the user's own list (a dozen or so items), never a whole store
CONTEXT_NEEDS_MAX = 15       # get_shopping_context lists at most this many items to look up at once
PRICE_STALE_DAYS = 7         # a price older than this is shown with its age and listed again under needs_price
PRICE_MAX_AGE_FUTURE_H = 1   # observed_at may not be in the future (clock skew allowance)
# "Bought dear / cheap / normal": the price per package against this user's own history of the same store product
# (observations + purchases, excluding the purchase itself) and the cheapest special seen recently. Thresholds are a judgement
# call (roughly the size of an ordinary Coles / WWS "price drop"), kept here so they can be tuned.
VERDICT_HISTORY_DAYS = 180   # history used for the average
VERDICT_MIN_POINTS = 2       # fewer reference prices -> no verdict
VERDICT_CHEAP_PCT = 0.08     # ≥ 8 % under the average -> cheap
VERDICT_DEAR_PCT = 0.12      # ≥ 12 % over the average -> dear
VERDICT_SPECIAL_DAYS = 21    # a special seen within this many days counts as "recently on special"
VERDICT_SPECIAL_DEAR = 1.25  # paying ≥ 25 % more than that recent special, not on special yourself -> dear
VERDICT_SPECIAL_CHEAP = 1.02 # paying within 2 % of the recent special (or less) -> cheap

# ---------------------------------------------------------------- usual items / 常买 (issue #103, docs/grocery-plan.md §3)
# Suggest marking a product as a usual item when this user bought it at least STAPLE_SUGGEST_MIN times in the last
# STAPLE_SUGGEST_WEEKS weeks (grocery_purchases). 3 in 8 weeks ≈ at least every other to every third week: the rhythm of the
# eggs / milk / bananas shop the issue describes, and enough points for a usable interval (2 gaps). A judgement call, not a
# published threshold; tune here. Only suggested: the user confirms (set_staple) before anything is marked.
STAPLE_SUGGEST_WEEKS = 8
STAPLES_FLAG = "grocery_staples"   # rollout flag (rollout/registry.py): off -> no suggestions, set_staple refused, generate adds none
STAPLE_SUGGEST_MIN = 3
STAPLE_SUGGEST_MAX = 8            # suggestions returned at once (get_shopping_context)
# Interval when set_staple is not given one: the median gap between this user's own purchases (rounded to whole days, at
# least STAPLE_MIN_DAYS), else a weekly shop. Bounds match the CHECK in migration 500.
STAPLE_DEFAULT_EVERY_DAYS = 7
STAPLE_MIN_DAYS = 1
STAPLE_MAX_DAYS = 180
STAPLE_UNITS = ("pack", "g", "ml", "pcs")   # pack = whole packages of the product

# ---------------------------------------------------------------- pantry goes down with logged meals (issue #104)
# Default of the per-user switch (grocery_settings.pantry_auto NULL). Off: changing someone's pantry without asking is the bigger
# surprise, and the pantry is rough (docs/grocery-plan.md §3); each user (or the AI on their word) turns it on. Owner's call (PR).
PANTRY_AUTO_DEFAULT = False
# Rollout flag (rollout/registry.py) over all of #104: off -> nothing is deducted whatever the switch says, set_pantry_auto is
# refused, no best-before reminders (best_before is still stored).
PANTRY_AUTO_FLAG = "pantry_auto"
# Best before: items within this many days (or already past) are listed in get_shopping_context / get_briefing and get the short
# 「x 天后到期」 label on the Today page. 3 days ≈ the "use within a few days" window of chilled food once it is close; a
# judgement call, tune here.
PANTRY_EXPIRY_DAYS = 3
PANTRY_EXPIRY_MAX = 10       # at most this many in get_briefing / get_shopping_context
# Amounts in a logged food's free-text amount ("2 个", "200 g", "1 碗（约 250g）") -> (factor, pantry unit). Only these; anything
# else (碗, 份, 勺, slices) is not deducted. Latin units need a non-letter after them ("1 large" is not litres).
AMOUNT_UNITS = {
    "kg": (1000, "g"), "千克": (1000, "g"), "公斤": (1000, "g"), "g": (1, "g"), "克": (1, "g"),
    "ml": (1, "ml"), "毫升": (1, "ml"), "l": (1000, "ml"), "升": (1000, "ml"),
    "个": (1, "pcs"), "只": (1, "pcs"), "枚": (1, "pcs"), "颗": (1, "pcs"), "根": (1, "pcs"), "pcs": (1, "pcs"),
    "pc": (1, "pcs"), "pieces": (1, "pcs"), "piece": (1, "pcs"),
}
