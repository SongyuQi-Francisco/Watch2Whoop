"""English for the Chinese `detail` of HTTP errors (issue #42). Applied once, in the exception handler in api.py, when the
request asked for English (X-Body-Lang: en); everyone else keeps the Chinese detail, so older clients and scripts are unchanged.
EXACT: whole detail -> English. PATTERNS: (regex on the Chinese, English with \\1 … groups) for details with numbers / names,
written below as templates: "{}" = any text in the Chinese, and in the English "{}" takes the groups in order ("{2}" picks one,
1-based). A captured group that is itself a fixed Chinese term (TERMS, e.g. 训练日) is translated too; other groups (food names,
numbers) are kept. Covers HTTPException details and the ValueError / LookupError … messages routers turn into details
(tests/test_i18n_server.py scans the source and fails on a Chinese detail without English).
Also used for the {"error": …} blocks of the Today page aggregate (web/views.py: localize())."""
import re

from .i18n import is_en

EXACT: dict[str, str] = {
    # ---- auth / sessions (api.py, records/api.py, web/)
    # ---- grocery-sync (sync/wws.py)
    "WWS 的 robots.txt 能读到了，但还没有经过确认的页面解析器：先人工确认条款和页面结构，再写 wws.py 的解析器": "WWS robots.txt is readable now, but there is no confirmed page parser yet: check the terms and the page structure first, then write the parser in wws.py",
    # ---- circles (friends/)
    "心电图不能通过圈子共享": "ECGs can't be shared through circles",
    "先在今天页重新确认同意书（新增了「圈子共享」一节）": "First confirm the updated consent on the Today page (it adds a section on circle sharing)",
    "没有这个圈子（或你不在里面）": "No such circle (or you are not in it)",
    "只有圈主能这样做": "Only the circle's owner can do that",
    "邀请码无效或已过期": "The invite code is invalid or expired",
    "这个邀请已失效": "This invite is no longer valid",
    "有效期和「永久有效」只能选一个": "Pick either a validity period or “never expires”, not both",
    "不能移除自己，用退出": "You can't remove yourself; leave the circle instead",
    "这个人不在圈子里": "This person is not in the circle",
    "要给邀请码或邀请 ID（只给一个）": "Give an invite code or an invite ID (just one)",
    "这个人不在你的圈子里": "This person is not in any of your circles",
    "今天已经点过了": "Already sent today",
    # ---- AI feedback (feedback/api.py)
    # ---- water (water/api.py, issue #125)
    "没有这条喝水记录": "No such water entry",
    "反馈记录只能看自己的": "You can only see your own feedback",
    "没有这条反馈（或不是你的）": "No such feedback (or it is not yours)",
    "缺少 Bearer token": "Missing Bearer token",
    "缺少 Bearer token（或没有登录）": "Missing Bearer token (or not signed in)",
    "token 无效或已吊销": "Invalid or revoked token",
    "只能在今天页登录后获取": "Only available after signing in on the Today page",
    "登录已过期或已退出，请重新输入 token": "Your session expired or you signed out; enter the token again",
    "用户写 me 或数字 ID": "User must be me or a numeric ID",
    "写操作只能写给自己": "You can only write your own data",
    "这个 token 没有 write 权限": "This token has no write permission",
    "这个登录没有 write 权限": "This sign-in has no write permission",
    "登录失败次数太多，请稍后再试": "Too many failed sign-ins; try again later",
    "失败次数太多，请稍后再试": "Too many failed attempts; try again later",
    "登录码无效、已用过或已过期": "Login code is invalid, used or expired",
    "激活码无效或已停用": "Activation code is invalid or disabled",
    "激活码无效、已用完或已过期": "Invite code is invalid, used up or expired",
    "没有登录": "Not signed in",
    # ---- account (account/api.py)
    "服务器还没配置发信，暂时不能用邮箱登录": "This server can't send e-mail yet, so e-mail sign-in is not available",
    "邮箱格式不对": "That e-mail address doesn't look right",
    "发送太频繁，请稍后再试": "Sending too often; try again later",
    "邮件发送失败，请稍后再试": "Couldn't send the e-mail; try again later",
    "验证码不对或已过期": "Wrong or expired code",
    "服务器还没配置「通过 Apple 登录」": "Sign in with Apple is not set up on this server",
    "Apple 登录无效，请重试": "Sign in with Apple failed; try again",
    "要确认删除：confirm = DELETE": "To confirm deletion send confirm = DELETE",
    # ---- not found (records/api.py _found and friends)
    "没有这条补剂": "Supplement not found",
    "没有这条化验结果": "Lab result not found",
    "没有这条化验项目": "Lab test not found",
    "没有这条事件": "Body event not found",
    "没有这条日记项": "Journal item not found",
    "没有这条心电图": "ECG not found",
    "没有这条提醒（或不是你的）": "Reminder not found (or not yours)",
    "没有这条观察笔记（或不是你的）": "Note not found (or not yours)",
    "没有这条已归档的笔记（或不是你的）": "No such archived note (or not yours)",
    "档案卡里没有这一项（或不是你的）": "No such profile card item (or not yours)",
    "档案卡里没有这一项已归档的（或不是你的）": "No such archived profile card item (or not yours)",
    "这天没有记这一项": "Nothing logged for this item on this day",
    # ---- records (records/api.py, records/store.py)
    "status 不能为空": "status cannot be empty",
    "value 和 value_text 至少要有一个": "Give value or value_text",
    "time_precision 不能为空": "time_precision cannot be empty",
    "key 只能用小写字母、数字、下划线，字母开头": "key may only use lowercase letters, digits and _, starting with a letter",
    "没有要修改的字段": "Nothing to change",
    "title 不能为空": "title cannot be empty",
    "occurred_at 不能为空": "occurred_at cannot be empty",
    "新日记项要有 label_zh": "A new journal item needs label_zh",
    "value 必须是有限的数": "value must be a finite number",
    # ---- date ranges (cardio/, rawdata/, training/, insights/)
    "start 要早于 end，最多 2 年": "start must be before end, at most 2 years",
    "start 要早于 end，最多 92 天": "start must be before end, at most 92 days",
    "end 不能早于 start": "end cannot be before start",
    "month 要写成 YYYY-MM": "month must be YYYY-MM",
    "period 要写 week 或 month": "period must be week or month",
    # ---- profile / consent (profile/)
    "个人资料只能看 / 改自己的": "You can only view / edit your own profile",
    "称呼不能清空": "Name cannot be empty",
    "时区不能清空": "Time zone cannot be empty",
    "称呼要 1–30 个字": "Name must be 1–30 characters",
    "生日不对（要在 1920 年以后、至少 10 岁）": "Birth date is not valid (after 1920, at least 10 years old)",
    "性别填 male / female，或者留空": "Sex: male / female, or leave it empty",
    "身高要在 100–230 cm 之间": "Height must be 100–230 cm",
    "时区不对（例：Australia/Sydney、Asia/Shanghai）": "Time zone is not valid (e.g. Australia/Sydney, Asia/Shanghai)",
    "语言填 zh / en，或者留空（跟随系统）": "Language: zh / en, or leave it empty (follow the device)",
    "目标体重要在 30–250 kg 之间": "Goal weight must be 30–250 kg",
    # ---- health history import (history/)
    "只能给自己导入": "You can only import for yourself",
    "分片太大": "Chunk too large",
    "空的分片": "Empty chunk",
    "文件是空的": "The file is empty",
    "请选「健康」app 导出的 export.zip（中文系统叫 导出.zip）": "Choose the export.zip exported from the Health app",
    "先在 app 里看完并同意《健康数据使用同意书》，再导入历史":
        "First read and accept the health data consent in the app, then import your history",
    "上一次导入还在进行中，完成后再传": "The previous import is still running; upload again when it's done",
    "还有一个没传完的文件；先取消它再传新的": "Another upload is unfinished; cancel it before uploading a new one",
    "服务器空间暂时不够，请过几个小时再试，或联系管理员":
        "The server is short of space; try again in a few hours or contact the admin",
    "没有这个导入": "Import not found",
    "这个导入已经不在上传状态": "This import is no longer uploading",
    "超过了开始时说的文件大小": "More data than the file size given at the start",
    "文件格式不对：请上传「健康」app 导出的 export.zip": "Wrong file format: upload the export.zip from the Health app",
    "正在导入，不能取消（几分钟就好）": "Importing now, can't cancel (it takes a few minutes)",
    "上传的文件不见了（可能已被清理）；请重新上传": "The uploaded file is gone (it may have been cleaned up); upload it again",
    # ---- memory / status (assistant/memory.py, memory_api.py)
    "键只能是小写字母、数字和下划线，字母开头，例 goals、schedule":
        "Keys may only use lowercase letters, digits and _, starting with a letter, e.g. goals, schedule",
    "value 不能为空（要清掉这一项，写「暂无」或在今天页删掉）": "value cannot be empty (to clear the item, delete it on the Today page)",
    "text 不能为空": "text cannot be empty",
    # ---- nutrition goals (goals/nutrition.py)
    "要写为什么改（reason，1–300 字）": "Say why you are changing it (reason, 1–300 characters)",
    "name 最多 40 字": "name: at most 40 characters",
    "没有要改的内容": "Nothing to change",
    "training_weekdays 是 1（周一）到 7（周日）": "training_weekdays are 1 (Monday) to 7 (Sunday)",
    "还没有饮食目标：先给训练日 / 休息日的热量和三大营养素":
        "No nutrition goals yet: first give calories and macros for training and rest days",
    # ---- sleep goal (goals/)
    "pace 和 step_days 要大于 0": "pace and step_days must be greater than 0",
    "睡眠时长 240–720 分钟": "Sleep duration must be 240–720 min",
    # ---- quick meals (assistant/meals.py)
    "模板至少要有一样食物": "A template needs at least one food",
    "没有这个模板（或不是你的）": "Template not found (or not yours)",
    "模板名 1–30 字": "Template name must be 1–30 characters",
    "全部克数都是 0，没东西可记": "All amounts are 0, nothing to log",
    "没有这次记录（或不是你的）": "Log not found (or not yours)",
    "这次记录已经撤销过了": "This log was already undone",
    "每样食物都要有名字（food）": "Every food needs a name (food)",
    # ---- recipes (assistant/recipes.py)
    "菜谱至少要有一样食材": "A recipe needs at least one ingredient",
    "没有这个菜谱（或不是你的）": "Recipe not found (or not yours)",
    "菜名 1–40 字": "Recipe name must be 1–40 characters",
    "servings 要在 0–50 之间": "servings must be 0–50",
    "servings 要在 0–10 之间": "servings must be 0–10",
    "每样食材都要有名字（food）": "Every ingredient needs a name (food)",
    "标签每个不超过 10 个字": "Each tag: at most 10 characters",
    # ---- food / training / weight logging (assistant/sparky_write.py)
    "kind 只能是 food / workout / set / weight": "kind must be food / workout / set / weight",
    "items 不能为空": "items cannot be empty",
    "exercises 不能为空": "exercises cannot be empty",
    "至少要有一个数值（weight_kg / body_fat_pct / waist_cm …）": "Give at least one value (weight_kg / body_fat_pct / waist_cm …)",
    "没给 id 时要给 meal（breakfast / lunch / dinner / snacks）和 food 精确匹配；或用 get_nutrition(include_entries=true) 查 id":
        "Without an id give meal (breakfast / lunch / dinner / snacks) and the exact food name",
    "time 要是 24 小时制 HH:MM": "time must be HH:MM (24-hour)",
    "portion 是相对现在份量的倍数（0.5 = 一半），要 > 0": "portion is a multiple of the current amount (0.5 = half), > 0",
    "reps、weight_kg、rir 至少给一个": "Give at least one of reps, weight_kg, rir",
    "训练 / 组要给 id（get_training_log 里有）": "A workout / set needs its id",
    "这是这个动作唯一的一组；删整次训练用 kind=workout": "This is the only set of this exercise; delete the workout instead",
    "没有要改的字段": "Nothing to change",
    "这个动作已经不在了": "This exercise no longer exists",
    "这次训练已经不在了": "This workout no longer exists",
    "每条都要有 food（吃了什么）和 amount（份量）": "Every entry needs food (what you ate) and amount",
    # ---- grocery (grocery/store.py)
    "nutrition 要是对象（每 100g）": "nutrition must be an object (per 100 g)",
    "package_unit 只能是 g / ml / pcs": "package_unit must be g / ml / pcs",
    "qty 要是 > 0 的数": "qty must be a number > 0",                       # usual items (grocery/staples.py, #103)
    "unit 只能是 pack / g / ml / pcs": "unit must be pack / g / ml / pcs",
    "best_before 要是 YYYY-MM-DD": "best_before must be YYYY-MM-DD",       # pantry best before (#104)
    "purine_level 只能是 low / medium / high": "purine_level must be low / medium / high",
    "没有这个商品（或不是你的）": "Product not found (or not yours)",
    "至少给一样": "Give at least one item",
    "计划是空的：给这周要吃的菜（recipe × 份数）、模板（template × 次数）或食材（food）":
        "The plan is empty: add this week's recipes (recipe × servings), templates (template × times) or foods",
    "至少加一样": "Add at least one item",
    "要给 item_ids": "Give item_ids",
    "商品名 1–60 字": "Product name must be 1–60 characters",
    "currency 用 3 位代码，例如 AUD / CNY": "currency is a 3-letter code, e.g. AUD / CNY",
    "条码只能是 6–14 位数字": "Barcode must be 6–14 digits",
    "条码查询太频繁，过一分钟再试": "Too many barcode lookups, try again in a minute",
    "Open Food Facts 暂时查不了（网络）": "Open Food Facts is unavailable right now (network)",
    "商品链接格式不对": "Invalid product link",
    "商品链接要是 https:// 开头的官网地址": "The product link must be an https:// address on the store's site",
    "只支持 Coles（coles.com.au/product/…）和 Woolworths（woolworths.com.au/shop/productdetails/…）的商品页":
        "Only Coles (coles.com.au/product/…) and Woolworths (woolworths.com.au/shop/productdetails/…) product pages are supported",
    "matches 最多 20 个、每个 ≤ 60 字": "matches: at most 20, each ≤ 60 characters",
    "新商品要有名字（name）": "A new product needs a name",
    "存货名 1–60 字": "Pantry item name must be 1–60 characters",
    "kind 只能是 recipe / template / food": "kind must be recipe / template / food",
    "amount 要是 > 0 的数（份数 / 次数 / 数量）": "amount must be a number > 0 (servings / times / quantity)",
    "每样要有名字（1–60 字）": "Every item needs a name (1–60 characters)",
    "实付价格要是 ≥ 0 的数": "Price paid must be a number ≥ 0",
    "food 要给名字": "food needs a name",
    # ---- AI settings / chat (ai/)
    "AI 设置只能管自己的（家人授权不包括 AI 配置）": "You can only manage your own AI settings (family shares don't include them)",
    "没有这个提供商（或不是你的）": "Provider not found (or not yours)",
    "这台服务器没有开通托底 AI": "This server has no backup AI",
    "没有这个 AI token（或已吊销）": "AI token not found (or revoked)",
    "没有这个对话": "Conversation not found",
    "不认识的密文版本": "Unknown ciphertext version",
    "余额接口没有返回人民币余额": "The balance API returned no CNY balance",
    "请在 Claude app 中使用身体管家": "Use Jarvis in the Claude app",
    "还没有设置 AI：在「我 → AI 设置」里选一个提供商": "No AI set up yet: pick a provider in Me → AI settings",
    "默认的 AI 提供商已停用，在「AI 设置」里启用或换一个": "Your default AI provider is disabled; enable it or pick another in AI settings",
    "说点什么，或者发一张图": "Say something, or send a photo",
    "没有这个待确认的操作": "No such pending action",
    "这个操作已经处理过了": "This action was already handled",
    "这次登录是只读的，不能写": "This sign-in is read-only, it can't write",
    "图片只支持 JPEG / PNG / WebP / GIF": "Images must be JPEG / PNG / WebP / GIF",
    "图片太大（每张不超过 5MB）": "Image too large (at most 5 MB each)",
    "托底 AI 还没开通": "The backup AI is not enabled",
    "这个提供商不属于你": "This provider is not yours",
    "保存的 API key 解不开了，请在「AI 设置」里重新填一次": "The saved API key can't be decrypted; enter it again in AI settings",
    "图片数据不是有效的 base64": "Image data is not valid base64",
    # ---- voice input (ai/asr.py)
    "录音格式要是 16 位单声道 WAV": "Audio must be 16-bit mono WAV",
    "太短了，按住多说一会儿": "Too short; hold and speak a little longer",
    "说得太快了，歇一下再试": "Too many in a row; wait a moment",
    "这台服务器没有开通语音识别，可以用键盘上的听写": "Voice input is not enabled on this server; use the keyboard's dictation",
    "不是有效的 WAV 录音": "Not a valid WAV recording",
    "语音识别暂时不可用，可以用键盘上的听写": "Voice input is unavailable right now; use the keyboard's dictation",
    "语音识别正忙，稍后再试": "Voice input is busy; try again later",
    # ---- SparkyFitness (sparky_client.py)
    "这把 SparkyFitness API key 不是只读的，Body Engine 只用只读 key": "This SparkyFitness API key is not read-only",
}

# Fixed Chinese terms that appear inside f-string details (captured groups are looked up here).
TERMS: dict[str, str] = {
    "训练日": "Training day", "休息日": "Rest day",
    "早餐": "breakfast", "午餐": "lunch", "晚餐": "dinner", "加餐 / 零食": "snacks",
    "补剂": "supplement", "化验结果": "lab result", "化验项目": "lab test", "事件": "body event", "日记项": "journal item",
    "心电图": "ECG",
    "评分": "scores", "提醒": "reminders", "训练": "training", "饮食": "nutrition", "睡眠": "sleep", "每日指标": "daily metrics",
    "体重": "weight", "日记": "journal", "身体事件": "body events", "动作限制": "exercise rules", "记忆": "memory",
    "饮食达标率": "nutrition adherence", "化验": "lab results", "长期记忆": "long-term memory",
}

_TEMPLATES: list[tuple[str, str]] = [
    # ---- memory recent notes (assistant/memory.py, #112)
    ("until 写 YYYY-MM-DD（近况的最后一天）或 recent（默认 {} 天），长期事实不填",
     "until: YYYY-MM-DD (the last day of the recent note) or recent (default {} days); leave it empty for a lasting fact"),
    ("until {} 已经过去了（今天 {}），过去的事写日记或事件", "until {} has already passed (today is {}); past events go in the journal or events"),
    ("近况最长 {} 天；更长的是长期事实（不填 until）或档案卡",
     "A recent note lasts at most {} days; anything longer is a lasting fact (no until) or a profile card item"),
    # ---- grocery (links.py, off.py)
    ("今天的条码查询用完了（每人每天 {} 次）", "No barcode lookups left today ({} per person per day)"),
    ("Open Food Facts 暂时查不了（HTTP {}）", "Open Food Facts is unavailable right now (HTTP {})"),
    ("every_days 要是 {}–{} 的整数（天）", "every_days must be a whole number of days, {}–{}"),
    ("商品链接 1–{} 字", "Product link: 1–{} characters"),
    ("超市只能是 {}", "Supermarkets can only be: {}"),
    # ---- grocery-sync (sync/coles.py, sync/http.py; admin CLI / run records)
    ("Coles 页面里没有 __NEXT_DATA__（可能是挑战页或改版了；页面标题「{}」，{} 字节）", "No __NEXT_DATA__ in the Coles page (a challenge page or a redesign; title \"{}\", {} bytes)"),
    ("Coles 页面结构变了（results 不是列表）", "The Coles page structure changed (results is not a list)"),
    ("Coles 页面结构变了（{}: {}）", "The Coles page structure changed ({}: {})"),
    ("Coles {} 第 1 页没有商品，但 noOfResults = {}", "Coles {}: no products on page 1 but noOfResults = {}"),
    ("到了每次最多 {} 个请求的上限", "Reached the limit of {} requests per run"),
    ("读不到 robots.txt（HTTP {}）", "Can't read robots.txt (HTTP {})"),
    ("robots.txt 不允许：{}", "Not allowed by robots.txt: {}"),
    ("读不到 robots.txt（{}）", "Can't read robots.txt ({})"),
    ("不认识的超市：{}（可用 {}）", "Unknown supermarket: {} (available: {})"),
    ("不认识的超市：{}", "Unknown supermarket: {}"),
    ("不是 {} 的商品页（要 /product/… 或 /shop/productdetails/<编号>/…）", "Not a {} product page (needs /product/… or /shop/productdetails/<id>/…)"),
    # ---- circles (friends/)
    ("不认识的共享范围：{}", "Unknown sharing scope: {}"),
    ("不认识的鼓励：{}", "Unknown cheer: {}"),
    ("排行榜只能是 {}", "Board must be one of: {}"),
    ("圈子名称 1–{} 字", "Circle name: 1–{} characters"),
    ("邀请有效期只能是 {} 天或永久有效", "Invite validity must be {} days, or never expires"),
    ("最多加入 {} 个圈子", "You can be in at most {} circles"),
    ("这个圈子已满（最多 {} 人）", "This circle is full (at most {} people)"),
    # ---- authorization
    ("没有查看用户 {} 的 {} 的授权", "No permission to view the {2} of user {1}"),
    ("没有查看用户 {} 的授权", "No permission to view user {}"),
    ("没有授权查看{}", "No permission to view {}"),          # web/views.py forbidden blocks
    ("用 cookie 登录时写操作要带 {}: 1 头", "Write requests with cookie sign-in need the header {}: 1"),
    # ---- not found / validation with a value
    ("没有这条{}（或不是你的）", "Not found (or not yours): {}"),
    ("没有这条{}", "Not found: {}"),
    ("{} 不能为空", "{} cannot be empty"),
    ("没有这个日记项：{}（先用日记项列表确认 key）", "No such journal item: {}"),
    ("没有这个日记项：{}", "No such journal item: {}"),
    ("{} 是是 / 否项，value 只能是 1（是）或 0（否）", "{} is a yes / no item: value must be 1 (yes) or 0 (no)"),
    ("没有用户 {}", "No user {}"),
    ("一次最多查 {} 天", "At most {} days at a time"),
    ("同意书已经更新到 {}，请刷新后再看一遍", "The consent was updated to {}; reload and read it again"),
    ("不能改 {}", "Can't change {}"),
    ("有效期 1–{} 天，或不填（一直有效直到停用）", "Valid for 1–{} days, or leave empty (valid until disabled)"),
    ("有效期 1–{} 小时", "Valid for 1–{} hours"),
    ("scopes 只能是 {}", "scopes must be {}"),
    # ---- history import
    ("文件太大（{}MB，最大 {}MB）；请联系管理员", "File too large ({} MB, max {} MB); contact the admin"),
    ("还没传完（{} / {}）", "Upload not finished ({} / {})"),
    # ---- memory
    ("value 太长（{} 字），档案卡每项不超过 {} 字，写摘要", "value too long ({} characters); at most {} per profile item"),
    ("{} 不是标准键（{}），新键要给 label（中文名）", "{} is not a standard key ({}); a new key needs a label"),
    ("观察笔记不超过 {} 字，写成一句话的事实", "Notes: at most {} characters, one sentence of fact"),
    ("topic 写档案卡标准键之一：{}；拿不准写 null", "topic must be one of the profile card's standard keys: {}; null if unsure"),
    ("origin 写 {} 或 null", "origin must be {} or null"),
    ("档案卡 {} 在这次修改之后又改过（或删了），不能自动撤回；用 update_profile_card 手动改回",
     "Profile item {} was changed (or deleted) after this edit, so it can't be undone automatically; change it back by hand"),
    ("观察笔记 {} 不存在（或不是你的、已归档）", "Note {} not found (or not yours, or archived)"),
    ("#{} 已经是一模一样的文字；保留那条、删掉这条就行", "#{} already has exactly this text; keep that one and delete this one"),
    ("观察笔记 {} 在这次修改之后又改过（或删了），不能自动撤回", "Note {} was changed (or deleted) after this edit, so it can't be undone automatically"),
    # ---- nutrition goals / food values (label = 训练日 / 休息日 or a food name)
    ("{}：缺 {}（第一版要给全热量和三大营养素）", "{}: missing {} (the first version needs calories and all three macros)"),
    ("{}：饱和脂肪上限不能高于总脂肪", "{}: saturated fat cap cannot be above total fat"),
    ("{}：饱和脂肪不能多于总脂肪", "{}: saturated fat cannot be more than total fat"),
    ("生效日期要在 {} 天前到 {} 天后之间", "Effective date must be from {} days ago to {} days ahead"),
    ("{}：{} 要是数字", "{}: {} must be a number"),
    ("{}：{} {} 不在 {}–{} 之间", "{}: {} {} is not within {}–{}"),
    ("{}：添加糖不能多于总糖（added_sugar_g {} g，sugar_g {} g，超出 {} g）", "{}: added sugar cannot be more than total sugar (added_sugar_g {} g, sugar_g {} g, {} g over)"),
    ("{}：添加糖不能多于总糖", "{}: added sugar cannot be more than total sugar"),
    ("{}：{} 不能多于碳水（{} {} g，碳水 {} g，超出 {} g）", "{}: {} cannot be more than carbs ({} {} g, carbs {} g, {} g over)"),
    ("{}：钠 {} mg 太多，是不是把 g 当成 mg 了", "{}: sodium {} mg is too much; g entered as mg?"),
    ("{}：{} 要是 ≥ 0 的数（不知道就 null）", "{}: {} must be a number ≥ 0 (null if unknown)"),
    ("{}：{} 不能多于碳水", "{}: {} cannot be more than carbs"),
    ("{}：糖不能多于碳水", "{}: sugar cannot be more than carbs"),
    ("{}：{} 要是 ≥ 0 的数", "{}: {} must be a number ≥ 0"),
    ("{}：{} 必须是 ≥ 0 的数", "{}: {} must be a number ≥ 0"),
    ("{}：{} 要是 > 0 的数", "{}: {} must be a number > 0"),
    # ---- sleep goal
    ("时间要写成 HH:MM（24 小时制），收到：{}", "Time must be HH:MM (24-hour), got: {}"),
    ("每档提前 {}–{} 分钟（生物钟一天只能挪 15–30 分钟）", "Each step moves {}–{} min earlier (the body clock shifts 15–30 min a day)"),
    ("每档 {}–{} 天", "Each step lasts {}–{} days"),
    # ---- quick meals / recipes
    ("meal 只能是 {}", "meal must be {}"),
    ("已经有叫「{}」的模板，用 update_meal_template 改", "A template called “{}” already exists"),
    ("模板里没有：{}", "Not in the template: {}"),
    ("{} 的{}已经记过「{}」，没有重复记（确实又吃了一份就用 allow_duplicates）",
     "“{3}” is already logged for {2} on {1}; not logged twice"),
    ("「{}」重复了，同一个模板里名字不能重复", "“{}” appears twice; names in one template must differ"),
    ("{}：克数 {} 不合理", "{}: {} g doesn't look right"),
    ("{}：红肉克数不能多于总克数", "{}: red meat grams cannot be more than the total"),
    ("{}：热量 {} kcal 和三大营养素算出来的 {} kcal 差太多", "{}: {} kcal is too far from the {} kcal the macros add up to"),
    ("{}：purine_level 只能是 low / medium / high", "{}: purine_level must be low / medium / high"),
    ("slot 只能是 {}", "slot must be {}"),
    ("已经有「{}」了，用 update_recipe 改", "“{}” already exists"),
    ("{}：store 只能是 {} 或 null", "{}: store must be {} or null"),
    ("slots 至少一个，只能是 {}", "slots: at least one of {}"),
    ("{} 要在 0–600 分钟之间", "{} must be 0–600 min"),
    ("新菜谱要有 {}", "A new recipe needs {}"),
    # ---- food / training / weight logging
    ("{} 没有这一组：{}（组的 id 在 get_training_log 里）", "{}: no such set: {}"),
    ("不支持回滚 {}", "Can't undo {}"),
    ("日期 {} 在未来（用户今天是 {}）；日期是行为发生的那天", "Date {} is in the future (today is {})"),
    ("日期 {} 早于 {} 天前，补这么早的记录请在 SparkyFitness 里手动加", "Date {} is more than {} days ago; add such old entries in SparkyFitness"),
    ("{}：{} kcal 太大，确认是不是把一整天或别的单位算进来了", "{}: {} kcal is too much; is that a whole day or another unit?"),
    ("{}：热量 {} kcal 和三大营养素算出来的 {} kcal （4×蛋白 + 4×碳水 + 9×脂肪 + 7×酒精）差太多，先核对",
     "{}: {} kcal is too far from the {} kcal the macros add up to; check it"),
    ("{}：sugary_drink 是 true / false / null", "{}: sugary_drink is true / false / null"),
    ("{}：次数 {} 不合理", "{}: {} reps doesn't look right"),
    ("{}：重量 {}kg 不合理（哑铃写单只、绳索写每侧）", "{}: {} kg doesn't look right (dumbbells per dumbbell, cables per side)"),
    ("{}：RIR 只能是 0–10", "{}: RIR must be 0–10"),
    ("{}：时长 {} 秒不合理", "{}: {} s doesn't look right"),
    ("{}：每组至少要有次数、重量或时长之一", "{}: every set needs reps, weight or duration"),
    ("这些动作在 SparkyFitness 动作库里对不上：{}。用候选里的名字或 exercise_id 再试；确实是新动作就让用户确认后用 create_missing_exercises=true",
     "These exercises are not in the SparkyFitness library: {}"),
    ("{} {}没有「{}」{}；用 get_nutrition(include_entries=true) 核对名字或拿 id", "No “{3}” in {2} on {1}{4}"),
    ("{} {}「{}」有 {} 条，不确定删哪条：{}。给 time 或 id 再试", "{4} entries of “{3}” in {2} on {1}: {5}. Give time or id"),
    ("{} 没有这条饮食记录：{}（用 get_nutrition(include_entries=true) 查 id 和日期）", "{}: no such food entry: {}"),
    ("{} 没有称重 / 体成分记录", "{}: no weigh-in / body composition entry"),
    ("{} 没有这条饮食记录（可能已经删了）", "{}: no such food entry (it may already be deleted)"),
    ("{}：至少要有一组", "{}: needs at least one set"),
    ("{} = {} 不在合理范围 {}–{}（体重用 kg）", "{} = {} is outside {}–{} (weight in kg)"),
    ("{} 没有这条记录：{}", "{}: no such entry: {}"),
    # ---- grocery
    ("nutrition 不认识：{}（可用 {}）", "Unknown nutrition fields: {} (allowed: {})"),
    ("category 只能是 {}", "category must be {}"),
    ("{}：package_size 和 package_unit 要一起给", "{}: give package_size and package_unit together"),
    ("{}：price 要是 ≥ 0 的数（每包）", "{}: price must be a number ≥ 0 (per pack)"),
    ("清单里没有 {}（或不是你的）", "Not on the list: {} (or not yours)"),
    ("{}：{} 每 100g 不能超过 100", "{}: {} per 100 g cannot be over 100"),
    ("{}：每 100g 热量 {} kcal 和三大营养素算出来的 {} kcal 差太多", "{}: {} kcal per 100 g is too far from the {} kcal the macros add up to"),
    ("{}：unit 只能是 g / ml / pcs", "{}: unit must be g / ml / pcs"),
    ("{}：quantity 要是 ≥ 0 的数", "{}: quantity must be a number ≥ 0"),
    ("{} 要给 ref_id", "{} needs ref_id"),
    ("没有商品 {}（或不是你的）", "No product {} (or not yours)"),
    ("{}：qty 要是 > 0 的数", "{}: qty must be a number > 0"),
    ("{} 最多 {} 字", "{}: at most {} characters"),
    # ---- AI chat / voice
    ("你用的是订阅（{}）：请在 {} 里和身体管家对话（已连上 Body Engine 的 MCP）", "You use a subscription ({}): talk to Jarvis in {}"),
    ("本月 AI 预算已用完（${} / ${}）：在「AI 设置」里调高预算，或下个月再用",
     "This month's AI budget is used up (${} / ${}): raise it in AI settings, or wait for next month"),
    ("一次最多 {} 张图片", "At most {} images at a time"),
    ("一条消息不超过 {} 字", "A message can be at most {} characters"),
    ("录音太大（最长 {} 秒）", "Recording too large (at most {} s)"),
    ("一条最长 {} 秒", "At most {} s per recording"),
    ("今天的语音识别用完了（每天 {} 条），可以用键盘上的听写", "Voice input used up for today ({} a day); use the keyboard's dictation"),
    ("语音识别出错（{}）", "Voice input failed ({})"),
    # ---- SparkyFitness
    ("连不上 SparkyFitness（{}）：{}", "Can't reach SparkyFitness ({}): {}"),
    ("用户 {} 还没连 SparkyFitness：{}", "User {} is not connected to SparkyFitness: {}"),
    ("用户 {} 的 SparkyFitness key 读不出来：{}", "Can't read the SparkyFitness key of user {}: {}"),
    ("找不到 SparkyFitness 只读 key：{}（deploy/sparkyfitness/bootstrap.py 生成）", "SparkyFitness read-only key not found: {}"),
    ("找不到 SparkyFitness 读写 key：{}（deploy/sparkyfitness/bootstrap.py 生成）", "SparkyFitness read-write key not found: {}"),
    ("{} 登录的不是用户 {} 连的 SparkyFitness 账号，不写", "{} is not the SparkyFitness account of user {}; not writing"),
]


def _compile(zh: str, en: str) -> tuple[re.Pattern, str]:
    parts = zh.split("{}")
    rx = re.compile("(.*?)".join(re.escape(p) for p in parts), re.S)
    n = iter(range(1, len(parts)))
    return rx, re.sub(r"\{(\d*)\}", lambda m: f"\\g<{m[1] or next(n)}>", en)


PATTERNS: list[tuple[re.Pattern, str]] = [_compile(zh, en) for zh, en in _TEMPLATES]


def translate(detail: str) -> str:
    if detail in EXACT:
        return EXACT[detail]
    if detail.startswith("SparkyFitness："):   # memory_api / reports: "SparkyFitness：<inner reason>"
        return "SparkyFitness: " + translate(detail.removeprefix("SparkyFitness："))
    for rx, en in PATTERNS:
        m = rx.fullmatch(detail)
        if m:
            groups = [TERMS.get(g, g) for g in m.groups()]
            return re.sub(r"\\g<(\d+)>", lambda x: groups[int(x[1]) - 1], en)
    return detail


def translate_detail(detail):
    """An HTTPException detail: a string, or a {"code", "message", …} dict (AI chat / voice errors)."""
    if isinstance(detail, str):
        return translate(detail)
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return {**detail, "message": translate(detail["message"])}
    return detail


def localize(text: str) -> str:
    """For texts built outside an HTTPException (web/views.py {"error": …} blocks): English when the request is."""
    return translate(text) if is_en() and isinstance(text, str) else text
