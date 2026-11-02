"""More Chinese gym names for the curated exercises, keyed by catalog slug (issue: log_workout found no candidate for
common Chinese names).

People say the same movement many ways ("杠铃背深蹲" / "杠铃后蹲" / "背蹲", "腿屈伸" / "腿伸展", "传统硬拉" / "常规硬拉" ...). The curated
list in catalog_data.py carries one standard name plus a few aliases; this file adds the other common wordings. They are merged into
the catalog rows when the rows are loaded (catalog.catalog_rows), so a deployment needs no re-seed.

Rules for editing: an alias belongs to exactly one slug (tests/test_ai_tool_robustness.py checks this after normalizing, and that
every slug exists); keep a bare ambiguous word (for example 卧推 without a bar type) out of this list; never point a wording at a
different movement just because it is similar. Aliases of the banned exercises (upright_row, behind_neck_*, dips) are here on
purpose: a wrong name must still land on the exercise the ban list knows."""

EXTRA_ALIASES: dict[str, list[str]] = {
    # ---- chest
    "barbell_bench_press": ["杠铃平卧推", "平卧推", "平板杠铃卧推", "卧推（杠铃）", "杠铃平卧推举"],
    "incline_barbell_bench_press": ["上斜杠铃推胸", "上斜卧推（杠铃）", "杠铃上斜推胸"],
    "dumbbell_bench_press": ["哑铃平卧推", "平板哑铃推胸", "哑铃推胸"],
    "incline_dumbbell_press": ["上斜哑铃推胸", "哑铃上斜卧推", "上斜哑铃推举"],
    "machine_chest_press": ["坐姿推胸", "推胸机", "器械胸推", "胸推机"],
    "pec_deck_fly": ["蝴蝶机飞鸟", "坐姿夹胸", "夹胸机"],
    "cable_fly_mid": ["绳索飞鸟", "龙门架飞鸟", "绳索对拉夹胸"],
    "dumbbell_fly": ["哑铃平板飞鸟", "哑铃夹胸", "平板飞鸟"],
    "push_up": ["伏地挺身"],
    # ---- back
    "lat_pulldown": ["下拉", "宽握下拉", "高位下拉（宽握）", "坐姿下拉", "颈前下拉", "背阔肌下拉"],
    "neutral_grip_pulldown": ["对握下拉", "V把下拉", "对握高位下拉"],
    "close_grip_underhand_pulldown": ["反握下拉", "反手下拉", "反握高位下拉", "窄握反手下拉"],
    "pull_up": ["引体", "宽握引体向上", "标准引体向上", "正握引体向上"],
    "chin_up": ["反握引体向上"],
    "seated_cable_row_close": ["坐姿绳索划船", "绳索划船", "低位划船", "低位绳索划船", "窄握划船", "坐姿拉背"],
    "seated_cable_row_wide": ["宽握划船", "坐姿绳索宽握划船"],
    "machine_row": ["坐姿器械划船", "器械坐姿划船", "划船器械"],
    "barbell_row_wide": ["杠铃划船", "俯身划船", "杠铃俯身划船"],
    "one_arm_dumbbell_row": ["单臂划船", "哑铃划船", "单手划船"],
    "t_bar_row": ["T型杆划船"],
    "back_extension_45": ["背伸展", "罗马椅背伸展", "挺身"],
    # ---- shoulders
    "overhead_barbell_press": ["杠铃肩推", "站姿杠铃推举", "坐姿杠铃推举", "杠铃推肩"],
    "dumbbell_shoulder_press": ["哑铃肩推", "哑铃肩上推举", "坐姿哑铃肩推"],
    "machine_shoulder_press": ["器械肩推", "坐姿肩推机", "推肩机"],
    "dumbbell_lateral_raise": ["侧平举（哑铃）", "哑铃侧举", "侧举"],
    "cable_lateral_raise": ["单臂绳索侧平举", "绳索侧举"],
    "reverse_pec_deck": ["反向飞鸟机", "后束蝴蝶机"],
    # ---- legs
    "barbell_back_squat": ["杠铃后蹲", "背蹲", "后蹲", "杠铃背蹲", "杠铃颈后深蹲", "传统深蹲", "深蹲（杠铃）", "杠铃背负深蹲"],
    "leg_press": ["腿举机", "倒蹬机", "腿蹬", "腿举（器械）"],
    "seated_leg_extension": ["腿伸展", "坐姿腿伸展", "腿屈伸机", "股四头肌伸展", "器械腿伸展"],
    "seated_leg_curl": ["坐姿腿屈曲", "腿屈曲", "器械腿弯举", "坐姿腿弯举机"],
    "lying_leg_curl": ["俯卧腿屈曲", "俯卧腿弯举机", "卧姿腿弯举"],
    "conventional_deadlift": ["杠铃硬拉", "常规硬拉", "标准硬拉"],
    "barbell_romanian_deadlift": ["杠铃罗马硬拉", "罗马硬拉", "直腿硬拉", "僵直腿硬拉"],
    "dumbbell_romanian_deadlift": ["哑铃罗马硬拉", "哑铃直腿硬拉"],
    "barbell_hip_thrust": ["杠铃臀桥推", "臀推（杠铃）"],
    "bulgarian_split_squat": ["保加利亚深蹲", "保加利亚单腿蹲"],
    "dumbbell_walking_lunge": ["哑铃箭步蹲", "箭步蹲", "弓步", "弓箭步", "行走箭步蹲"],
    "standing_calf_raise": ["站姿小腿提踵", "小腿提踵", "史密斯提踵"],
    "seated_calf_raise": ["坐姿小腿提踵"],
    # ---- arms
    "dumbbell_bicep_curl": ["哑铃二头弯举", "二头弯举", "哑铃交替弯举", "哑铃肱二头弯举"],
    "ez_bar_curl": ["曲杠弯举", "EZ杆弯举", "杠铃弯举", "杠铃二头弯举"],
    "cable_bicep_curl": ["绳索二头弯举", "龙门架二头弯举"],
    "cable_triceps_pushdown": ["三头下压", "绳索三头下压", "三头肌下压", "直杆下压"],
    "overhead_cable_triceps_extension": ["绳索臂屈伸（过头）", "过头臂屈伸", "三头过头伸展"],
    "skull_crusher": ["法式推举", "仰卧三头伸展", "哑铃臂屈伸", "杠铃臂屈伸"],
    # ---- core / other
    "plank": ["平板", "平板撑"],
    "hanging_knee_raise": ["悬垂举腿", "悬垂抬腿", "吊杠举腿"],
    "cable_crunch": ["绳索卷腹", "龙门架卷腹", "跪姿卷腹"],
    "kettlebell_swing": ["壶铃甩", "壶铃摆荡"],
    "incline_walk": ["跑步机坡度走", "爬坡走", "坡度步行"],
    # ---- banned by default: wrong wordings must still reach the exercise the ban list knows
    "upright_row": ["直立提拉", "杠铃直立划船", "哑铃直立划船"],
    "behind_neck_press": ["颈后推肩", "颈后肩推"],
    "behind_neck_pulldown": ["颈后高位下拉"],
    "dips": ["双杠撑", "双杠下压"],
}
