"""English twins of the static explanation entries in glossary.py (issue #42), picked by glossary.metric / lab / status / condition
when the request language is en. Same facts and sources as the Chinese text. Only the texts the page shows are here
(title, what, read, recovery, tips, avoid, unit); `read` lists (range, label) in the same order as the Chinese entry, the tone comes from it.
Non-text fields (keywords, match, metrics, scope, series) stay in the Chinese entry: they match the user's own Chinese notes.
Conditions are keyed by their Chinese title. tests/test_explain.py checks that every Chinese entry has a complete twin here.
"""
from .glossary_trends_en import TREND_METRICS_EN

METRICS_EN: dict[str, dict] = {
    "recovery": {
        "title": "Recovery",
        "what": "A 0–100 score computed each morning from last night's HRV, resting HR, sleep performance and respiratory rate, "
                "compared with your own baseline of the last 60 days. Weights: HRV 60%, resting HR 20%, sleep performance 15%, "
                "respiratory rate 5%. Matching your baseline gives about 57.",
        "read": [("67–100", "Green"), ("34–66", "Yellow"), ("0–33", "Red")],
        "recovery": "Green: your body is ready for high strain. Yellow: train as planned, don't push to your limit. "
                    "Red: mostly easy activity and rest. Compare only with yourself; day-to-day swings are normal, "
                    "adjust only after several low days in a row.",
        "tips": ["2–3 red days in a row: cut back or rest, and check sleep, alcohol and illness first",
                 "Even when green, don't stack many hard days; plan rest days"],
    },
    "hrv": {
        "title": "HRV (heart rate variability)",
        "what": "The variation between consecutive heartbeats (ms). Apple Watch records SDNN, mostly during sleep and at rest. "
                "High HRV means the parasympathetic system (rest and digest) dominates: you are relaxed and recovering well. "
                "It drops with stress, fatigue, short sleep, alcohol and illness.",
        "read": [("Above baseline", "Good"), ("Within ±1 SD of baseline", "Normal"), ("Below the baseline band", "Low")],
        "recovery": "The most important Recovery input (60% weight): the higher your HRV relative to your own baseline, the higher Recovery. "
                    "It varies a lot between people (age, genetics), so don't compare with others; watch the 7-day average against your baseline.",
        "tips": ["Keep a regular schedule and sleep 7+ hours", "No alcohol or big meals within 3 hours of bed",
                 "Lower values 1–2 days after hard training are normal",
                 "Low for several days with a rising resting HR: you may be getting sick; cut back and rest"],
    },
    "resting_hr": {
        "title": "Resting HR",
        "what": "Heartbeats per minute when fully relaxed (bpm), lowest during sleep. \"Last night's resting HR\" here is the average "
                "of the lowest 5 minutes of sleep. The better your aerobic fitness, the lower it usually is.",
        "read": [("Below baseline", "Good"), ("Baseline ±2 bpm", "Normal"), ("2–5 above baseline", "High"),
                 ("≥ 5 above baseline", "Clearly high")],
        "recovery": "One of the Recovery inputs (20% weight): lower than baseline is better. More than 5 bpm above baseline together with "
                    "lower HRV is common with fatigue, short sleep, dehydration, alcohol or early illness. Most adults are at 60–100 bpm; "
                    "40–60 is common in people who train regularly.",
        "tips": ["Drink enough water; rehydrate after sauna and training", "Less alcohol at night; don't eat dinner too late or too much",
                 "Regular aerobic training (Zone 2) lowers it slowly over time"],
    },
    "sleep_hours": {
        "title": "Sleep duration",
        "what": "Time actually asleep (not time lying awake in bed), from the watch's sleep stages.",
        "read": [("≥ 7 h", "Enough"), ("6–7 h", "A bit short"), ("< 6 h", "Not enough")],
        "recovery": "Adults need 7–9 hours a night, more with heavy training. Short sleep lowers HRV, raises resting HR and builds sleep debt; "
                    "it also increases appetite and hurts strength gains and fat loss.",
        "tips": ["Fix your wake time first (weekends too), then move bedtime earlier step by step",
                 "Get sunlight within 30 minutes of waking",
                 "No coffee after noon; finish training at least 3 hours before bed", "Keep the bedroom cool, dark and quiet"],
    },
    "sleep_score": {
        "title": "Sleep score",
        "what": "0–100, four weighted parts: duration 40% (7–9 hours scores full), sleep stages 20% (enough deep and REM sleep), "
                "consistency 20% (how far bedtime is from the previous 14 nights) and interruptions 20%.",
        "read": [("≥ 80", "Good"), ("60–79", "Fair"), ("< 60", "Poor")],
        "recovery": "A low sleep score usually means low Recovery too. Find the part pulling the score down and fix that first.",
        "tips": ["Low consistency: sleep and wake at the same time every day", "Low duration: secure 7 hours first",
                 "Many awakenings: less water and alcohol before bed, a cooler bedroom"],
    },
    "sleep_efficiency": {
        "title": "Sleep efficiency",
        "what": "Time asleep ÷ time in bed. Scrolling in bed, taking long to fall asleep and long awakenings all lower it.",
        "read": [("≥ 85%", "Good"), ("75–84%", "Fair"), ("< 75%", "Low")],
        "recovery": "Low efficiency means time in bed isn't turning into real sleep, so recovery suffers.",
        "tips": ["Go to bed only when sleepy; use the bed only for sleep",
                 "Can't sleep for more than 20 minutes: get up, do something quiet, return when sleepy",
                 "Avoid screens and bright light in the last hour before bed"],
    },
    "sleep_debt": {
        "title": "Sleep debt",
        "what": "Weighted average of how much less than 8 hours you slept on each of the last 14 nights (recent nights weigh more; "
                "extra sleep pays part of it back). It is added to tonight's sleep need (up to +2 hours).",
        "read": [("≤ 1 h", "Low"), ("1–2 h", "Elevated"), ("> 2 h", "High")],
        "recovery": "The more sleep debt, the harder it is to recover. Pay it back by going to bed earlier and sleeping a bit more "
                    "over the next few nights, not with one long lie-in.",
        "tips": ["Go to bed 30 minutes earlier for the next few nights", "Keep daytime naps to 20–30 minutes and before 3 pm"],
    },
    "sleep_consistency": {
        "title": "Sleep consistency",
        "what": "How stable your bedtime and wake time are (compared with the previous 14 nights; within 15 minutes costs nothing).",
        "read": [("≥ 80", "Consistent"), ("60–79", "Fair"), ("< 60", "Irregular")],
        "recovery": "With a stable body clock you sleep deeper and fall asleep more easily; shifting times are like daily jet lag.",
        "tips": ["Fix your wake time first; bedtime will follow", "On weekends don't get up more than 1 hour later than usual"],
    },
    "bedtime": {
        "title": "Bedtime",
        "what": "Average start time of the main sleep over the last 7 nights.",
        "read": [("Before midnight", "Good"), ("Midnight–2 am", "Late"), ("After 2 am", "Very late")],
        "recovery": "A very late bedtime usually means too little sleep and an irregular schedule. Deep sleep is concentrated in the first "
                    "half of the night, so sleeping late makes it easier to lose deep sleep.",
        "tips": ["Move it 15–30 minutes earlier at a time, hold for a week, then move again",
                 "Dim the lights and put the phone away an hour before bed"],
    },
    "vo2max": {
        "title": "VO₂max (maximal oxygen uptake)",
        "what": "The most oxygen your body can use per kilogram of body weight per minute at full effort (mL/kg·min), "
                "a measure of cardiorespiratory fitness. Apple Watch estimates it during outdoor walks, runs and hikes.",
        "read": [("Top 20% for your age and sex", "Excellent"), ("Top 20–40%", "Good"), ("Middle", "Average"),
                 ("Bottom 20–40%", "Fair"), ("Bottom 20%", "Poor")],
        "recovery": "Not a Recovery input; it is a long-term marker: higher VO₂max goes with a lower resting HR and faster recovery, "
                    "and is strongly linked to longevity and cardiovascular health. It changes slowly; look at trends over months.",
        "tips": ["2–3 Zone 2 cardio sessions a week (you can talk but not sing, e.g. incline walking), 30–60 minutes each",
                 "One interval session a week (e.g. 4×4 minutes hard with 3 minutes easy walking in between)",
                 "Losing fat by itself raises it (it is per kg of body weight)"],
    },
    "strain": {
        "title": "Strain",
        "what": "How much cardiovascular load you took on in a day, 0–21, converted from the time your heart rate spent in each zone "
                "(TRIMP); the higher it gets, the harder it is to raise.",
        "read": [("0–9", "Light"), ("10–13", "Moderate"), ("14–17", "High"), ("18–21", "Very high")],
        "recovery": "Match strain to Recovery: on green days you can go above 14, on red days stay under 10. "
                    "Recovery usually drops the day after high strain.",
        "tips": ["Strength training keeps heart rate low, so strain reads low; look at muscle load",
                 "After several high-strain days, plan a low-strain day"],
    },
    "weight": {
        "title": "Weight",
        "what": "Your latest weigh-in (manual entries first). Look at the 7-day average: water, salt, carbs and bowel movements "
                "can shift a single day by 1–2 kg.",
        "read": [("−0.3 to −1.0 kg per week", "On track"), ("Losing > 1 kg per week", "Too fast"), ("Gaining > 0.3 kg per week", "Rising")],
        "recovery": "Losing weight too fast (too big a calorie deficit) often comes with fatigue, early waking, lower strength and "
                    "falling Recovery.",
        "tips": ["Weigh every morning after the toilet, before eating or drinking",
                 "Adjust calories by the weekly average, not single days"],
    },
}

METRICS_EN.update(TREND_METRICS_EN)

LABS_EN: dict[str, dict] = {
    "uric_acid": {
        "title": "Uric acid",
        "what": "A product of purine metabolism, cleared mainly by the kidneys. A high level (in adult men usually > 420 μmol/L) is "
                "called hyperuricaemia; staying high for long lets urate crystals form, causing gout and kidney stones, and it is "
                "linked to metabolic syndrome and fatty liver.",
        "read": [("≤ 360", "Ideal"), ("360–420", "High normal"), ("> 420", "High")],
        "recovery": "Hard training, dehydration, heavy sweating in the sauna, fasting or losing weight too fast can all raise uric acid "
                    "for a while.",
        "tips": ["Drink plenty of water (at least 2 L of urine a day); 300–500 ml before and after the sauna",
                 "Less organ meat, rich meat broth and some seafood; limit red meat",
                 "No beer or sugary drinks (fructose raises uric acid)", "Steady weight loss by itself lowers uric acid",
                 "At follow-up tests, tell your doctor which supplements you take"],
    },
    "alt": {
        "title": "ALT (alanine aminotransferase)",
        "what": "An enzyme found mainly in liver cells; it leaks into the blood when they are damaged. The most common cause of a high "
                "level is fatty liver; alcohol, medicines or supplements and viral hepatitis are other causes. Very hard training "
                "(muscle damage) can also raise it for a while.",
        "read": [("Within range", "Normal"), ("Above the upper limit, < 2×", "Mildly high"), ("≥ 2× the upper limit", "Clearly high")],
        "recovery": "Skip hard training for 2 days before a follow-up test, so a rise from muscle isn't mistaken for a liver problem.",
        "tips": ["Losing 7–10% of body weight clearly improves fatty liver", "Less alcohol, sugary drinks and saturated fat",
                 "Supplements (especially \"detox\" ones) can affect liver enzymes too; ask your doctor whether to stop them before a follow-up"],
    },
    "triglycerides": {
        "title": "Triglycerides",
        "what": "A type of fat in the blood; surplus calories (especially refined carbs, sugar and alcohol) are turned into triglycerides "
                "and stored. Fasting < 1.7 mmol/L is normal; high levels are linked to fatty liver, insulin resistance and "
                "cardiovascular risk.",
        "read": [("< 1.7", "Normal"), ("1.7–2.2", "Borderline high"), ("≥ 2.3", "High")],
        "recovery": "Regular exercise and fat loss are among the most effective ways to lower triglycerides; changes usually show "
                    "within weeks to months.",
        "tips": ["Fewer sugary drinks, desserts and refined carbs; put most carbs around training", "Stop or cut down alcohol",
                 "Eat more fish (or take fish oil as your doctor advises)", "Fast 8–12 hours before the follow-up test"],
    },
}

STATUS_EN: dict[str, dict] = {
    "to_goal": {
        "title": "To goal",
        "what": "The gap between your latest weight and your goal weight, with the weeks left estimated from your recent weekly change.",
        "read": [],
        "recovery": "",
        "tips": ["Track progress by the weekly average weight, not single days",
                 "The pace matters more than the finish line: hold 0.5–1% of body weight per week"],
    },
    "goal_weight": {
        "title": "Goal weight",
        "what": "The weight to reach in this phase, set by you or Jarvis.",
        "read": [],
        "recovery": "",
        "tips": ["Set goals in phases, with a checkpoint for each phase"],
    },
    "training_week": {
        "title": "Workouts this week",
        "what": "Days with a logged workout in the last 7 days (workouts logged in SparkyFitness).",
        "read": [("≥ 4", "Good"), ("2–3", "A bit few"), ("0–1", "Very few")],
        "recovery": "Match the number of sessions to Recovery: if you train often enough but Recovery stays low, cut intensity, "
                    "not the number of sessions.",
        "tips": ["Start \"Traditional Strength Training\" on the Watch when you lift, so strain is counted correctly"],
    },
    "red_meat_week": {
        "title": "Red meat (7 days)",
        "what": "Grams of red meat (beef, lamb, pork) eaten in the last 7 days; journal entries first, otherwise estimated from food logs.",
        "read": [("< 80% of the limit", "Good"), ("80–100%", "Near the limit"), ("> limit", "Over")],
        "recovery": "",
        "tips": ["Get protein mainly from dairy, eggs, chicken and fish, from varied sources",
                 "Choose lean cuts of red meat; skip rich meat stews and broths"],
    },
    "phase": {
        "title": "Current phase",
        "what": "The overall direction of your training and diet right now, kept up to date by Jarvis from your plan.",
        "read": [],
        "recovery": "",
        "tips": [],
    },
}

# Keyed by the Chinese title of the condition in glossary.CONDITIONS
CONDITIONS_EN: dict[str, dict] = {
    "肩关节内旋受限": {
        "title": "Limited shoulder internal rotation",
        "what": "The shoulder's range of internal rotation (arm at your side, forearm turning towards the belly, or reaching up your back "
                "with the back of the hand) is smaller than normal. Common causes are a tight posterior capsule and tight infraspinatus "
                "and teres minor; it often comes with rounded shoulders and poor shoulder-blade control. Clicking without pain is "
                "usually a tendon sliding over bone and is generally nothing to worry about.",
        "read": [],
        "recovery": "With limited mobility the shoulder takes load at awkward angles, which concentrates stress on the front of the "
                    "shoulder; over time this can lead to impingement or pain.",
        "tips": ["On pressing movements keep elbows about 45° from the body; prefer dumbbells and a neutral grip",
                 "Do gentle sleeper stretches and cross-body stretches daily, staying pain-free",
                 "Band external rotations and shoulder-blade retraction and depression; practise \"set the shoulders, then pull\"",
                 "Leave 1–2 reps in reserve per set to avoid shrugging to compensate at failure",
                 "Pain, night pain, weakness, catching, or no improvement in 4–6 weeks: see a physio"],
        "avoid": ["Upright rows (lifting with the shoulder internally rotated)", "Behind-the-neck press / behind-the-neck pulldown",
                  "Very deep dips", "Heavy face pulls (they easily turn into shrugs)"],
    },
    "圆肩": {
        "title": "Rounded shoulders",
        "what": "The shoulders sit forward and turned in, often with a tight pec minor and weak upper-back and rear shoulder-blade "
                "muscles; long sitting and pushing more than pulling make it worse.",
        "read": [],
        "recovery": "",
        "tips": ["Do at least as many pulling sets as pushing sets (1:1); add more rows",
                 "Stretch the pec minor and pec major", "Train shoulder-blade retraction and depression and the lower traps"],
        "avoid": ["Training only pushes and no pulls", "Heavy pulls where you shrug to compensate"],
    },
    "睡眠障碍": {
        "title": "Sleep problems",
        "what": "Any trouble with sleep: difficulty falling asleep, waking at night or too early, too little sleep or a disrupted "
                "schedule (e.g. habitually going to bed very late and struggling to get up), affecting energy and recovery the next day. "
                "Trouble falling or staying asleep at least 3 nights a week for over 3 months is called chronic insomnia.",
        "read": [],
        "recovery": "Sleep is the base of recovery: poor sleep lowers HRV and raises resting HR, and cuts the results of training and fat loss.",
        "tips": ["Get up at the same time every day (weekends too) and get sunlight within 30 minutes of waking",
                 "No coffee after noon; no alcohol or hard training within 3 hours of bed",
                 "Dim the lights and put the phone away an hour before bed",
                 "Can't sleep for more than 20 minutes: get up, return to bed when sleepy",
                 "For long-term insomnia, CBT-I (cognitive behavioural therapy for insomnia) is the first choice; snoring with "
                 "choking awake or extreme daytime sleepiness: see a doctor to rule out sleep apnoea"],
        "avoid": ["Catching up on sleep at weekends", "Alcohol to help you sleep (it disrupts the second half of the night)"],
    },
    "早醒": {
        "title": "Waking too early",
        "what": "Waking earlier than planned and not getting back to sleep. Common with high stress, too big a calorie deficit, "
                "alcohol and an irregular schedule; it can also be a sign of a mood problem.",
        "read": [],
        "recovery": "During a fat-loss phase, early waking is often a sign of too big a calorie deficit; if you are also tired, slow "
                    "the weight loss down.",
        "tips": ["Keep weight loss to 0.5–1% of body weight per week", "Include some carbs at dinner", "Black out the bedroom"],
        "avoid": ["Too big a calorie deficit", "Drinking in the evening"],
    },
    "作息不规律": {
        "title": "Irregular sleep schedule",
        "what": "Late and shifting bed and wake times. Habitually going to bed very late and struggling to get up is close to "
                "\"delayed sleep phase\": the whole body clock has shifted later.",
        "read": [],
        "recovery": "A disrupted body clock means less deep sleep and lower HRV; even with enough total sleep, recovery is somewhat worse.",
        "tips": ["Fix your wake time first, then move bedtime 15–30 minutes earlier each week",
                 "Bright light in the morning, avoid bright light at night", "Keep training and meal times as fixed as possible too"],
        "avoid": ["Moving bedtime much earlier in one go (it tends to bounce back)"],
    },
    "脂肪肝": {
        "title": "Fatty liver",
        "what": "Too much fat stored in liver cells; the most common type is metabolic fatty liver linked to excess weight and insulin "
                "resistance, often with high ALT and triglycerides.",
        "read": [],
        "recovery": "",
        "tips": ["Losing 7–10% of body weight clearly improves it", "Regular cardio plus strength training",
                 "Less alcohol, sugary drinks and saturated fat"],
        "avoid": ["Alcohol", "Sugary drinks and fruit juice"],
    },
    "窦性心动过缓": {
        "title": "Sinus bradycardia",
        "what": "Resting heart rate below 60 bpm with an otherwise normal rhythm. Very common in people who exercise regularly; "
                "without symptoms such as dizziness, fainting or fatigue it is usually physiological.",
        "read": [],
        "recovery": "",
        "tips": ["See a doctor if you get dizzy, black out, faint, or your exercise capacity suddenly drops"],
        "avoid": [],
    },
    "运动时低血糖感": {
        "title": "Low blood sugar during exercise",
        "what": "Training fasted or on too few carbs lets blood sugar drop, causing shaking, sweating, palpitations, weakness and dizziness.",
        "read": [],
        "recovery": "",
        "tips": ["Eat a full meal 1.5–2 hours before training (50–70 g carbs, 20–30 g protein)",
                 "Keep fast-acting sugar at hand for heavy sessions such as leg day"],
        "avoid": ["Lifting heavy on an empty stomach"],
    },
    "减脂增肌": {
        "title": "Body recomposition",
        "what": "Losing fat while keeping or gaining muscle (recomp) with a small calorie deficit, enough protein and progressive "
                "strength training. Easiest for beginners and people returning to training.",
        "read": [],
        "recovery": "",
        "tips": ["Protein 1.6–2.2 g per kg of body weight", "Keep the calorie deficit small (lose 0.5–1% of body weight per week)",
                 "Double progression in strength training: add reps first, then weight"],
        "avoid": ["Too big a calorie deficit"],
    },
}
