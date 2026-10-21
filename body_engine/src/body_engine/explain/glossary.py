"""Static explanation entries (Chinese, short; English twins in glossary_en.py, picked by metric() / lab() / status() / condition()
when the request language is en, issue #42). Each entry:
title, what (what it is), read [(range, label, tone)] (how to read it), recovery (how it relates to recovery),
tips [general advice], avoid [things to avoid], keywords (used to pick matching sentences from the profile card);
conditions may also list metrics whose personal numbers are attached (personal.CALC keys).
tone is good / mid / bad / plain, the same classes the Today page uses for badges.
Ranges follow the scoring code (scores/params.py) or common clinical / guideline cut-offs; they are general references, not diagnoses.
"""

METRICS: dict[str, dict] = {
    "recovery": {
        "title": "恢复分",
        "what": "每天早上用昨晚的 HRV、静息心率、睡眠表现和呼吸频率，和你自己近 60 天的基线比较算出的 0–100 分。"
                "权重：HRV 60%、静息心率 20%、睡眠表现 15%、呼吸频率 5%。和基线持平约 57 分。",
        "read": [("67–100", "绿", "good"), ("34–66", "黄", "mid"), ("0–33", "红", "bad")],
        "recovery": "绿：身体准备好承受高负荷；黄：按计划练、别冲极限；红：以轻松活动和休息为主。"
                    "只和自己比，单日波动正常，连续几天偏低才要调整。",
        "tips": ["连续 2–3 天红：减量或休息，先查睡眠、饮酒、生病",
                 "绿也别连续多天大负荷，安排好休息日"],
        "keywords": ["恢复"],
    },
    "hrv": {
        "title": "HRV 心率变异性",
        "what": "相邻两次心跳间隔的波动（毫秒）。Apple Watch 记录的是 SDNN，多在睡眠和安静时测。"
                "HRV 高说明副交感神经（休息、消化）占主导，身体放松、恢复得好；压力、疲劳、睡眠不足、饮酒、生病时会下降。",
        "read": [("高于基线", "好", "good"), ("基线 ±1 个标准差内", "正常", "plain"), ("低于基线下沿", "偏低", "bad")],
        "recovery": "恢复分最重要的输入（权重 60%）：HRV 相对你自己的基线越高，恢复分越高。"
                    "个体差异很大（年龄、遗传），不要和别人比，看 7 天均值相对基线的变化。",
        "tips": ["规律作息、睡够 7 小时以上", "睡前 3 小时内不喝酒、不吃大餐",
                 "高强度训练后 1–2 天偏低是正常的", "连续几天偏低且静息心率升高：可能是生病前兆，减量休息"],
        "keywords": ["HRV", "心率变异"],
    },
    "resting_hr": {
        "title": "静息心率",
        "what": "完全放松时每分钟心跳次数（bpm），夜里睡着时最低。这里的「昨晚静息心率」是睡眠中最低 5 分钟的平均。"
                "有氧能力越好，静息心率通常越低。",
        "read": [("比基线低", "好", "good"), ("基线 ±2 bpm", "正常", "plain"), ("比基线高 2–5", "偏高", "mid"),
                 ("比基线高 ≥ 5", "明显偏高", "bad")],
        "recovery": "恢复分的输入之一（权重 20%）：比基线越低越好。比基线高 5 bpm 以上同时 HRV 下降，常见于疲劳、"
                    "睡眠不足、脱水、饮酒或生病早期。一般成人 60–100 bpm，常训练的人 40–60 也常见。",
        "tips": ["多喝水，桑拿和训练后补液", "晚上少喝酒、晚餐别太晚太撑", "长期规律有氧（Zone 2）能让它慢慢下降"],
        "keywords": ["静息心率", "心动过缓", "心率"],
    },
    "sleep_hours": {
        "title": "睡眠时长",
        "what": "实际睡着的时间（不算躺在床上醒着的时间），来自手表的睡眠阶段。",
        "read": [("≥ 7 小时", "够", "good"), ("6–7 小时", "偏少", "mid"), ("< 6 小时", "不够", "bad")],
        "recovery": "成人建议每晚 7–9 小时，训练量大时需要更多。睡不够会压低 HRV、抬高静息心率、累积睡眠债，"
                    "还会让食欲变大、力量和减脂效果变差。",
        "tips": ["先固定起床时间（周末也一样），再逐步把入睡提前", "起床 30 分钟内晒太阳",
                 "午后不喝咖啡，训练在睡前 3 小时前结束", "卧室凉、暗、安静"],
        "keywords": ["睡眠", "睡", "作息"],
    },
    "sleep_score": {
        "title": "睡眠分",
        "what": "0–100 分，四项加权：时长 40%（7–9 小时满分）、睡眠阶段 20%（深睡和 REM 够不够）、"
                "规律性 20%（入睡时间和前 14 晚差多少）、中途醒来 20%。",
        "read": [("≥ 80", "好", "good"), ("60–79", "一般", "mid"), ("< 60", "差", "bad")],
        "recovery": "睡眠分低通常意味着恢复分也会低。看哪一项拉低了分数，就先改那一项。",
        "tips": ["规律性分低：每天同一时间睡和起", "时长分低：先保证 7 小时", "醒来多：睡前少喝水和酒，卧室别太热"],
        "keywords": ["睡眠"],
    },
    "sleep_efficiency": {
        "title": "睡眠效率",
        "what": "睡着的时间 ÷ 在床上的时间。躺着刷手机、迟迟睡不着、半夜醒很久都会拉低它。",
        "read": [("≥ 85%", "好", "good"), ("75–84%", "一般", "mid"), ("< 75%", "低", "bad")],
        "recovery": "效率低说明在床上的时间没有变成有效睡眠，恢复打折扣。",
        "tips": ["困了再上床，床只用来睡觉", "睡不着超过 20 分钟就起来做点安静的事，困了再回去", "睡前 1 小时远离屏幕和强光"],
        "keywords": ["睡眠", "失眠"],
    },
    "sleep_debt": {
        "title": "睡眠债",
        "what": "近 14 晚每晚比 8 小时少睡的部分加权平均（越近的晚上权重越大，多睡可以抵掉一部分）。"
                "会加到今晚的睡眠需求里（最多 +2 小时）。",
        "read": [("≤ 1 小时", "少", "good"), ("1–2 小时", "偏多", "mid"), ("> 2 小时", "多", "bad")],
        "recovery": "睡眠债越多，恢复越难。还债靠接下来几晚早点睡、多睡一点，不靠一次补觉。",
        "tips": ["接下来几晚每晚提前 30 分钟上床", "白天小睡控制在 20–30 分钟、下午 3 点前"],
        "keywords": ["睡眠", "作息"],
    },
    "sleep_consistency": {
        "title": "睡眠规律性",
        "what": "每天入睡、起床时间稳不稳定（和前 14 晚相比，差 15 分钟以内不扣分）。",
        "read": [("≥ 80", "规律", "good"), ("60–79", "一般", "mid"), ("< 60", "不规律", "bad")],
        "recovery": "生物钟稳定时睡得更深、更容易入睡；时间来回变就像每天倒时差。",
        "tips": ["先固定起床时间，入睡时间会慢慢跟上", "周末起床别比平时晚 1 小时以上"],
        "keywords": ["作息", "规律", "入睡"],
    },
    "bedtime": {
        "title": "入睡时间",
        "what": "近 7 晚主睡眠开始时间的平均。",
        "read": [("24 点前", "好", "good"), ("0–2 点", "偏晚", "mid"), ("2 点以后", "很晚", "bad")],
        "recovery": "入睡太晚通常意味着睡不够、作息不规律，深睡集中在前半夜，晚睡更容易丢掉深睡。",
        "tips": ["每次只提前 15–30 分钟，稳定一周再提前", "睡前 1 小时调暗灯光、放下手机"],
        "keywords": ["入睡", "作息", "凌晨"],
    },
    "vo2max": {
        "title": "VO₂max 最大摄氧量",
        "what": "全力运动时身体每公斤体重每分钟能用的最大氧气量（mL/kg·min），衡量心肺耐力。"
                "Apple Watch 在户外步行、跑步、徒步时估算。",
        "read": [("同龄同性别前 20%", "优秀", "good"), ("前 20–40%", "良好", "good"), ("中间", "中等", "plain"),
                 ("后 20–40%", "一般", "mid"), ("后 20%", "较差", "bad")],
        "recovery": "不直接进恢复分，是长期指标：VO₂max 越高，静息心率越低、恢复越快，也和寿命、心血管健康强相关。"
                    "它变化很慢，看几个月的趋势。",
        "tips": ["每周 2–3 次 Zone 2 有氧（能说话但唱不了歌，例如爬坡快走）每次 30–60 分钟",
                 "每周 1 次间歇（如 4×4 分钟高强度、中间 3 分钟慢走）", "减脂本身会让它（按体重算）上升"],
        "keywords": ["有氧", "爬坡", "心肺"],
    },
    "strain": {
        "title": "负荷 strain",
        "what": "一天里心血管负担的大小，0–21，按全天心率在各区间的时间（TRIMP）换算，越往上越难涨。",
        "read": [("0–9", "轻", "plain"), ("10–13", "中", "good"), ("14–17", "高", "mid"), ("18–21", "很高", "bad")],
        "recovery": "负荷要和恢复匹配：恢复绿可以到 14 以上，红的日子控制在 10 以下。负荷高的第二天恢复分通常会降。",
        "tips": ["力量训练心率不高，strain 会偏低，看肌肉负荷", "连续几天高负荷后安排低负荷日"],
        "keywords": ["负荷", "训练"],
    },
    "weight": {
        "title": "体重",
        "what": "最近一次称重（手动记录优先）。看 7 天均值，单日受水分、盐、碳水、排便影响能差 1–2 kg。",
        "read": [("每周 −0.3 ~ −1.0 kg", "合适", "good"), ("每周掉 > 1 kg", "偏快", "mid"), ("每周涨 > 0.3 kg", "上升", "mid")],
        "recovery": "掉秤太快（热量缺口太大）常伴随疲劳、早醒、力量下降和恢复分走低。",
        "tips": ["每天早上起床、上完厕所、吃喝前称", "按周均体重调热量，不看单日"],
        "keywords": ["体重", "减重", "掉秤"],
    },
}

# Trends tab metrics and status cards (glossary_trends.py)
from .glossary_trends import TREND_METRICS  # noqa: E402

METRICS.update(TREND_METRICS)

# Lab items: same key as lab_results.test_key / status items
LABS: dict[str, dict] = {
    "uric_acid": {
        "title": "尿酸",
        "what": "嘌呤代谢的产物，主要由肾脏排出。偏高（成年男性一般 > 420 μmol/L）叫高尿酸血症，长期偏高会析出尿酸盐结晶，"
                "引起痛风、肾结石，也和代谢综合征、脂肪肝相关。",
        "read": [("≤ 360", "理想", "good"), ("360–420", "正常偏高", "mid"), ("> 420", "偏高", "bad")],
        "recovery": "高强度训练、脱水、桑拿大量出汗、断食或减重过快都会让尿酸短期升高。",
        "tips": ["多喝水（让每天尿量 2 L 以上），桑拿前后各补 300–500 ml", "少吃内脏、浓肉汤、部分海鲜，红肉控制量",
                 "不喝啤酒和含糖饮料（果糖会升尿酸）", "平稳减重本身能降尿酸", "复查时告诉医生在吃哪些补剂"],
        "keywords": ["尿酸", "嘌呤", "痛风"],
    },
    "alt": {
        "title": "ALT 谷丙转氨酶",
        "what": "主要在肝细胞里的酶，肝细胞受损时会漏到血里。偏高最常见的原因是脂肪肝，也可能来自饮酒、药物或补剂、"
                "病毒性肝炎；大强度训练（肌肉损伤）也会让它短期升高。",
        "read": [("正常范围内", "正常", "good"), ("高于上限 < 2 倍", "轻度偏高", "mid"), ("≥ 2 倍上限", "明显偏高", "bad")],
        "recovery": "复查前 2 天别做高强度训练，避免把肌肉来源的升高误当成肝的问题。",
        "tips": ["体重减 7–10% 能明显改善脂肪肝", "少酒精、少含糖饮料、少饱和脂肪", "补剂（尤其「护肝」类）也可能影响肝酶，复查前问医生要不要停"],
        "keywords": ["ALT", "转氨酶", "肝"],
    },
    "triglycerides": {
        "title": "甘油三酯",
        "what": "血液里的一种脂肪，多余的热量（特别是精制碳水、糖和酒）会被转成甘油三酯储存。"
                "空腹 < 1.7 mmol/L 算正常，偏高和脂肪肝、胰岛素抵抗、心血管风险有关。",
        "read": [("< 1.7", "正常", "good"), ("1.7–2.2", "边缘偏高", "mid"), ("≥ 2.3", "偏高", "bad")],
        "recovery": "规律运动和减脂是降甘油三酯最有效的办法之一，通常几周到几个月就能看到变化。",
        "tips": ["少含糖饮料、甜点和精制碳水，碳水多放在训练前后", "戒酒或少喝", "多吃鱼（或按医嘱吃鱼油）", "复查要空腹 8–12 小时"],
        "keywords": ["甘油三酯", "血脂", "脂肪"],
    },
}

# Status card items that are not metrics or labs (key -> entry)
STATUS: dict[str, dict] = {
    "to_goal": {
        "title": "距目标",
        "what": "最近体重和目标体重的差，按最近每周的变化估算还要几周。",
        "read": [],
        "recovery": "",
        "tips": ["按周均体重看进度，不看单日", "减重速度比终点更重要：稳住每周 0.5–1% 体重"],
        "keywords": ["目标", "体重"],
    },
    "goal_weight": {
        "title": "目标体重",
        "what": "这一阶段要到的体重，由你或管家设定。",
        "read": [],
        "recovery": "",
        "tips": ["分阶段设目标，每个阶段设检查点"],
        "keywords": ["目标", "体重"],
    },
    "training_week": {
        "title": "本周训练",
        "what": "近 7 天有训练记录的天数（SparkyFitness 里记的训练）。",
        "read": [("≥ 4 次", "好", "good"), ("2–3 次", "偏少", "mid"), ("0–1 次", "很少", "bad")],
        "recovery": "训练次数要和恢复匹配：次数够、但恢复连续偏低时，减的是强度而不是次数。",
        "tips": ["练完在 Watch 上开「传统力量训练」，负荷才算得准"],
        "keywords": ["训练", "计划"],
    },
    "red_meat_week": {
        "title": "红肉 7 天",
        "what": "近 7 天吃的红肉（牛、羊、猪）克数，日记里记的优先，否则从饮食记录估算。",
        "read": [("< 上限 80%", "好", "good"), ("80–100%", "接近上限", "mid"), ("> 上限", "超了", "bad")],
        "recovery": "",
        "tips": ["蛋白质优先奶、蛋、鸡、鱼，来源分散", "红肉选瘦的部位，少喝炖肉浓汤"],
        "keywords": ["红肉", "嘌呤", "饱和脂肪"],
    },
    "phase": {
        "title": "当前阶段",
        "what": "现在训练和饮食的大方向，由管家按你的计划维护。",
        "read": [],
        "recovery": "",
        "tips": [],
        "keywords": ["阶段", "计划"],
    },
}

# Conditions / judgements the butler writes as free text (matched by keyword in label + value + note)
CONDITIONS: list[dict] = [
    {
        "match": ["内旋"],
        "title": "肩关节内旋受限",
        "what": "肩关节内旋（手臂贴在身侧、前臂往肚子方向转，或手背往后够背）的活动范围比正常小。常见原因是肩后侧的关节囊和"
                "冈下肌、小圆肌偏紧，经常和圆肩、肩胛控制差一起出现。只有弹响、不痛，一般是肌腱滑过骨头，不用太担心。",
        "read": [],
        "recovery": "活动度受限时，肩在不舒服的角度下负重，容易把压力集中到肩前侧，久了可能发展成撞击或疼痛。",
        "tips": ["推类动作手肘和身体约 45°，优先哑铃和中立握", "每天做温和的睡姿牵拉（sleeper stretch）、交叉牵拉，不痛为限",
                 "弹力带外旋、肩胛后缩下沉，练「先沉肩再拉」", "每组留 1–2 次余力，避免力竭时耸肩代偿",
                 "疼痛、夜间痛、无力、卡住，或 4–6 周没改善：看 physio"],
        "avoid": ["直立划船（肩内旋位上举）", "颈后推举 / 颈后下拉", "很深的双杠臂屈伸", "过重的面拉（容易变成耸肩）"],
        "keywords": ["肩", "内旋", "弹响", "推", "肘"],
    },
    {
        "match": ["圆肩", "体态"],
        "title": "圆肩",
        "what": "肩膀向前、向内收，常伴随胸小肌偏紧、上背和肩胛后侧的肌肉偏弱，久坐和推多拉少都会加重。",
        "read": [],
        "recovery": "",
        "tips": ["拉和推的组数至少 1:1，多做划船类", "牵拉胸小肌、胸大肌", "练肩胛后缩下沉和下斜方"],
        "avoid": ["只练推、不练拉", "耸肩代偿的大重量拉"],
        "keywords": ["圆肩", "肩", "体态", "耸肩"],
    },
    {
        "match": ["睡眠障碍", "失眠", "睡不着", "入睡困难"],
        "title": "睡眠障碍",
        "what": "泛指睡眠出了问题：入睡困难、半夜易醒或早醒、睡得不够或作息紊乱（比如习惯很晚才睡、早上起不来），"
                "第二天精神和恢复受影响。每周 3 晚以上、持续 3 个月以上的入睡或维持困难叫慢性失眠。",
        "read": [],
        "recovery": "睡眠是恢复的基础：睡不好会压低 HRV、抬高静息心率，训练效果和减脂都会打折扣。",
        "tips": ["每天同一时间起床（周末也一样），起床 30 分钟内晒太阳", "午后不喝咖啡，睡前 3 小时内不喝酒、不剧烈训练",
                 "睡前 1 小时调暗灯光、放下手机", "睡不着超过 20 分钟就起来，困了再回床",
                 "长期失眠首选认知行为治疗（CBT-I），打鼾憋醒或白天极困要看医生排查睡眠呼吸暂停"],
        "avoid": ["靠周末补觉", "睡前喝酒助眠（会破坏后半夜睡眠）"],
        "keywords": ["睡", "作息", "早醒", "入睡", "失眠"],
        "metrics": ["bedtime", "sleep_hours", "sleep_efficiency"],
    },
    {
        "match": ["早醒"],
        "title": "早醒",
        "what": "比计划早醒、醒了又睡不回去。常见于压力大、热量缺口太大、饮酒、作息不规律，也可能是情绪问题的信号。",
        "read": [],
        "recovery": "减脂期早醒常是热量缺口过大的信号，伴随疲劳时要放慢减重速度。",
        "tips": ["减重速度控制在每周 0.5–1% 体重", "晚餐保证一些碳水", "卧室遮光"],
        "avoid": ["热量缺口过大", "晚上喝酒"],
        "keywords": ["早醒", "睡", "热量"],
        "metrics": ["sleep_hours", "weight"],
    },
    {
        "match": ["晚睡", "作息", "熬夜", "睡眠时相"],
        "title": "作息不规律",
        "what": "入睡和起床时间晚且不固定。长期很晚才睡、早上起不来，接近「睡眠时相延迟」：生物钟整体往后推了。",
        "read": [],
        "recovery": "生物钟紊乱会让深睡变少、HRV 变低，即使总时长够，恢复也会差一些。",
        "tips": ["先固定起床时间，再每周把入睡提前 15–30 分钟", "早上见强光、晚上避强光", "训练、吃饭时间也尽量固定"],
        "avoid": ["一次大幅提前（容易反弹）"],
        "keywords": ["作息", "入睡", "起床", "凌晨"],
        "metrics": ["bedtime", "sleep_consistency"],
    },
    {
        "match": ["脂肪肝"],
        "title": "脂肪肝",
        "what": "肝细胞里堆积的脂肪过多，最常见的是和超重、胰岛素抵抗相关的代谢性脂肪肝，常伴随 ALT、甘油三酯偏高。",
        "read": [],
        "recovery": "",
        "tips": ["体重减 7–10% 能明显改善", "规律有氧 + 力量训练", "少酒精、少含糖饮料、少饱和脂肪"],
        "avoid": ["饮酒", "含糖饮料和果汁"],
        "keywords": ["肝", "ALT", "饱和脂肪", "酒"],
    },
    {
        "match": ["心动过缓"],
        "title": "窦性心动过缓",
        "what": "静息心率低于 60 次 / 分、但心律本身正常。经常运动的人很常见；没有头晕、晕厥、乏力等症状时多为生理性。",
        "read": [],
        "recovery": "",
        "tips": ["出现头晕、眼前发黑、晕厥或运动能力突然下降时看医生"],
        "avoid": [],
        "keywords": ["心动过缓", "心率"],
    },
    {
        "match": ["低血糖"],
        "title": "运动时低血糖感",
        "what": "空腹或碳水太少时训练，血糖下降出现手抖、出汗、心慌、乏力、头晕。",
        "read": [],
        "recovery": "",
        "tips": ["练前 1.5–2 小时吃正餐（碳水 50–70 g、蛋白 20–30 g）", "练腿等大负荷时手边备快糖"],
        "avoid": ["空腹练大重量"],
        "keywords": ["低血糖", "练前", "空腹", "碳水"],
    },
    {
        "match": ["减脂增肌", "recomp"],
        "title": "减脂增肌",
        "what": "在小的热量缺口下保持足够蛋白质和渐进式力量训练，同时减掉脂肪、保住或增加肌肉（recomp）。新手和刚恢复训练的人最容易做到。",
        "read": [],
        "recovery": "",
        "tips": ["蛋白质每公斤体重 1.6–2.2 g", "热量缺口别太大（每周掉 0.5–1% 体重）", "力量训练双进阶：先加次数再加重量"],
        "avoid": ["热量缺口过大"],
        "keywords": ["减脂", "增肌", "蛋白", "热量", "计划"],
    },
]

# Status keys that are the same thing as a metric explanation
STATUS_AS_METRIC = {"recovery": "recovery", "sleep_hours": "sleep_hours", "sleep_debt": "sleep_debt",
                    "bedtime": "bedtime", "weight": "weight", "hrv": "hrv", "resting_hr": "resting_hr",
                    "vo2max": "vo2max"}


def condition_for(text: str) -> dict | None:
    """First condition whose match words appear in the text (label + value + note of a status item), in the request language.
    The match words stay Chinese: they match the user's own notes."""
    c = next((c for c in CONDITIONS if any(w.lower() in text.lower() for w in c["match"])), None)
    return condition(c) if c else None


# ---------------------------------------------------------------- language (issue #42)
from ..i18n import is_en  # noqa: E402
from .glossary_en import CONDITIONS_EN, LABS_EN, METRICS_EN, STATUS_EN  # noqa: E402

TEXT_FIELDS = ("title", "what", "read", "recovery", "tips", "avoid", "unit")   # the fields an English twin replaces


def localize(e: dict, en: dict | None) -> dict:
    """The entry in the request language: the English twin's texts over the Chinese entry (non-text fields kept);
    `read` in the twin is [(range, label)], the tone comes from the Chinese entry. No twin: the Chinese entry."""
    if not en or not is_en():
        return e
    out = {**e, **{k: v for k, v in en.items() if k != "read"}}
    if "read" in en:
        out["read"] = [(rng, label, t) for (rng, label), (_, _, t) in zip(en["read"], e.get("read", []))]
    return out


def metric(key: str) -> dict:
    return localize(METRICS[key], METRICS_EN.get(key))


def lab(key: str) -> dict:
    return localize(LABS[key], LABS_EN.get(key))


def status(key: str) -> dict:
    return localize(STATUS[key], STATUS_EN.get(key))


def condition(c: dict) -> dict:
    return localize(c, CONDITIONS_EN.get(c["title"]))


def all_entries() -> dict:
    """Every static entry in the request language (GET /v1/explain/glossary)."""
    return {"metrics": {k: metric(k) for k in METRICS}, "labs": {k: lab(k) for k in LABS},
            "status": {k: status(k) for k in STATUS}, "conditions": [condition(c) for c in CONDITIONS]}
