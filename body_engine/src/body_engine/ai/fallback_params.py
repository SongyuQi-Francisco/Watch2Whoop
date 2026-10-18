"""Parameters of the fallback AI (ai/fallback.py). Change prices here when DeepSeek changes them.

Prices: DeepSeek's Chinese pricing page https://api-docs.deepseek.com/zh-cn/quick_start/pricing, checked 2026-10-18,
model deepseek-flash (= DeepSeek-V4.1-Flash-1017), CNY per million tokens:
  peak (Beijing time Mon–Fri 9:00–12:00, 14:00–18:00, excluding Chinese public holidays): cache hit 0.04, cache miss 2, output 8;
  off-peak = half price.
The page lists CNY directly, so no exchange rate is needed. Every call is charged at the PEAK price (conservative):
the real bill is never higher than what we count, and public holidays need no calendar. The quota is therefore at least ¥3 of real spend.
"""

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
DISPLAY_NAME = "DeepSeek Flash"
KEY_FILE_NAME = "deepseek_api_key"        # secrets/deepseek_api_key (Ali), or BODY_DEEPSEEK_API_KEY_FILE

PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing（2026-10-18 核对，按高峰价）"
PRICE_CACHE_HIT_CNY = 0.04                # per million input tokens served from DeepSeek's context cache
PRICE_CACHE_MISS_CNY = 2.0                # per million input tokens not in the cache
PRICE_OUTPUT_CNY = 8.0                    # per million output tokens

MONTHLY_QUOTA_CNY = 3.0                   # per person per natural month (user's time zone); admin can override per person
MAX_OUTPUT_TOKENS = 2048                  # per model call (short Chinese answers; log_food arguments fit easily)
MAX_ROUNDS = 6                            # model calls chained for one user message (the general chat allows 8)
MAX_MESSAGES = 40                         # per conversation: history is resent every call, longer = dearer
CHARS_PER_TOKEN = 2.0                     # pre-call estimate of prompt tokens (Chinese ~1–1.5 chars/token, JSON ~3–4): errs high
RATE_PER_MINUTE = 10                      # model calls per person per minute (one message is usually 1–3 calls)
RATE_PER_DAY = 150                        # model calls per person per day (local day)
VISION = True                             # deepseek-flash reads images (https://api-docs.deepseek.com/guides/vision; tested 2026-10-18:
                                          # a 900x600 JPEG ~350 prompt tokens). Images may only be in user messages (ours are).
IMAGE_TOKENS = 1024                       # DeepSeek bills at most 1024 tokens per image: used for the pre-call worst case

BALANCE_STOP_CNY = 2.0                    # account balance below this: fallback pauses for everyone (doctor ✗)
BALANCE_WARN_CNY = 10.0                   # below this, or below what the users may still spend this month: doctor ⚠ "top up"
BALANCE_MAX_AGE_S = 600                   # re-check the balance at most every 10 minutes (on chat or doctor)
TOPUP_URL = "https://platform.deepseek.com/top_up"
