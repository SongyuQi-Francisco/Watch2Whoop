"""Grocery parameters: categories, rounding, and the "high" thresholds used to flag products (docs/grocery-plan.md).

Per-100 g thresholds follow the UK Food Standards Agency front-of-pack "traffic light" guidance (Guide to creating a front of pack
nutrition label, FSA / DH 2016): high = sat fat > 5 g, total sugars > 22.5 g, salt > 1.5 g (sodium ≈ salt / 2.5 -> 600 mg) per 100 g.
For drinks (per 100 ml) the FSA lines are halved: sat fat > 2.5 g, sugars > 11.25 g, salt > 0.75 g (sodium 300 mg).
Red meat and short rib caps reuse rules/params.py (the user's weekly caps); purine levels reuse the meal-template scale.
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
            "红肉": "Red meat", "牛肋条": "Short rib"}   # every product flag (FLAG_LABELS + store.product_flags)

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
