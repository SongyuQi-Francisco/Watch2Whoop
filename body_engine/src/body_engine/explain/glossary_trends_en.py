"""English twins of glossary_trends.TREND_METRICS (same keys; texts and display units only, see glossary_en.py).
Status names follow the original products (Garmin HRV Status / Training Status, Oura Resilience, MCTQ chronotypes)."""

TREND_METRICS_EN: dict[str, dict] = {
    "resp_rate": {
        "title": "Respiratory rate", "unit": "/min",
        "what": "Breaths per minute during sleep (the median the watch measures overnight). Adults are usually at 12–20, "
                "and each person's value is very stable night to night.",
        "read": [("Within 1 of baseline", "Normal"), ("1–2 above baseline", "High"), ("> 2 above baseline", "Clearly high")],
        "recovery": "One of the Recovery inputs (5% weight). A sudden rise is common with fever, respiratory infection, alcohol or overtraining.",
        "tips": ["Clearly high two nights in a row: watch for a cold, cut back and rest", "Less alcohol before bed"],
    },
    "spo2": {
        "title": "Blood oxygen (SpO₂)",
        "what": "The share of oxygenated haemoglobin in the blood, measured intermittently by the watch during sleep. Usually 95–100% in healthy people.",
        "read": [("≥ 95%", "Normal"), ("90–94%", "Low"), ("< 90%", "Very low")],
        "recovery": "Not a Recovery input; used for the illness signal. Repeated low values at night can be linked to sleep apnoea, "
                    "a cold or altitude; readings depend on how snugly the watch is worn.",
        "tips": ["One low night is usually a fit problem", "Low for a long time with snoring and daytime sleepiness: ask your GP for a sleep assessment"],
    },
    "wrist_temp": {
        "title": "Wrist temperature deviation",
        "what": "The difference between your wrist skin temperature during sleep and your own average of the previous 60 days. "
                "It shows change only; it is not body temperature. Apple Watch needs a few nights of wear before it has data.",
        "read": [("Within ±0.5 °C", "Normal"), ("0.5–1 °C higher", "High"), ("> 1 °C higher", "Clearly high")],
        "recovery": "A rise is common around a fever, after alcohol, a late big meal, going to bed straight after the sauna, or a hot bedroom.",
        "tips": ["Wait at least 1–2 hours after the sauna before bed", "Keep the bedroom around 18–20 °C"],
    },
    "illness": {
        "title": "Illness risk",
        "what": "Combines a rise in resting HR, a drop in HRV, a rise in respiratory rate (all against your own baseline), a rise in "
                "wrist temperature and low night-time blood oxygen into a 0–100 score, similar to Oura's Symptom Radar. "
                "It is a signal, not a diagnosis.",
        "read": [("< 30", "Low"), ("30–59", "Watch"), ("≥ 60", "High")],
        "recovery": "When several of these get worse together, your body is more likely fighting an infection or overly fatigued; "
                    "Recovery is usually low too.",
        "tips": ["Watch / high: only light activity that day, sleep more, drink more water",
                 "High for 2–3 days or with symptoms: rest, and see a GP if needed"],
    },
    "recovery_index": {
        "title": "Lowest HR timing",
        "what": "Where in the night your lowest 5-minute heart rate occurs (0% = just fallen asleep, 100% = just before waking). "
                "Similar to Oura's Recovery Index. Needs raw heart rate during sleep; it will be more accurate once the app syncs "
                "beat-to-beat (RR) intervals.",
        "read": [("< 50%", "First half"), ("50–74%", "Second half"), ("≥ 75%", "Very late")],
        "recovery": "The earlier your heart rate reaches its low, the sooner your body is in recovery mode. A very late low is common "
                    "after alcohol before bed, a late dinner, hard training in the evening or a sauna.",
        "tips": ["Finish dinner at least 3 hours before bed", "Do hard training during the day where possible"],
    },
    "sleep_need": {
        "title": "Sleep need", "unit": "h",
        "what": "How long to sleep tonight: a base of 8 hours + sleep debt (up to +2 hours) + an extra for the previous day's strain. "
                "Bars are actual sleep, the dashed line is the need.",
        "read": [("Actual ≥ 85% of need", "Enough"), ("70–84%", "A bit short"), ("< 70%", "Not enough")],
        "recovery": "Actual ÷ need is sleep performance, one of the Recovery inputs.",
        "tips": ["On high-need nights, going to bed earlier works better than sleeping in the next day"],
    },
    "sleep_performance": {
        "title": "Sleep performance",
        "what": "Time actually asleep ÷ that night's sleep need (WHOOP's original Sleep Performance definition).",
        "read": [("≥ 85%", "Good"), ("70–84%", "Fair"), ("< 70%", "Poor")],
        "recovery": "One of the Recovery inputs (15% weight).",
        "tips": ["Fix your wake time first, then move bedtime earlier step by step"],
    },
    "sleep_perf_whoop": {
        "title": "Sleep performance (combined)",
        "what": "A combined score modelled on WHOOP's newer Sleep Performance: meeting your sleep need 60% + consistency 20% + "
                "sleep efficiency 20% (WHOOP also uses sleep stress, which we have no data for, so its weight goes to the other three). "
                "For comparison only; not a Recovery input.",
        "read": [("≥ 85", "Good"), ("70–84", "Fair"), ("< 70", "Poor")],
        "recovery": "More complete than duration alone: enough sleep at irregular times or with many awakenings still pulls the score down.",
        "tips": ["Consistency pulling it down: fix your wake time", "Efficiency pulling it down: go to bed only when sleepy"],
    },
    "restorative": {
        "title": "Restorative sleep", "unit": "h",
        "what": "Time in deep + REM sleep and its share of total sleep (WHOOP's Restorative Sleep). Deep sleep repairs the body; "
                "REM is linked to memory and mood.",
        "read": [("Share ≥ 40%", "Good"), ("30–39%", "Fair"), ("< 30%", "Low")],
        "recovery": "With little restorative sleep, recovery suffers even when total sleep is long enough. Alcohol clearly suppresses REM, "
                    "and a late bedtime loses the deep sleep of the first half of the night.",
        "tips": ["No alcohol within 3 hours of bed", "Keep a regular schedule and go to bed earlier"],
    },
    "awakenings": {
        "title": "Awakenings", "unit": "times",
        "what": "Times awake for more than 5 minutes during the main sleep (not counting before falling asleep or lying in after waking).",
        "read": [("0–1", "Few"), ("2–3", "Several"), ("≥ 4", "Many")],
        "recovery": "Frequent waking breaks up deep and REM sleep; Recovery and energy suffer the next day.",
        "tips": ["Less water and alcohol in the 2 hours before bed", "Keep the bedroom cool, dark and quiet"],
    },
    "daylight": {
        "title": "Time in daylight", "unit": "min",
        "what": "Apple Watch's estimate of the time you spend outdoors in daylight each day.",
        "read": [("≥ 60 min", "Enough"), ("20–59 min", "A bit little"), ("< 20 min", "Little")],
        "recovery": "Enough daylight during the day steadies the body clock and makes you sleepy earlier at night; especially useful "
                    "for late sleepers.",
        "tips": ["Go outside for 10–20 minutes of sunlight within 30 minutes of waking", "Do your cardio outdoors during the day"],
    },
    "recovery_vs_strain": {
        "title": "Recovery vs strain",
        "what": "7-day averages of Recovery and strain on one chart (left axis Recovery 0–100, right axis strain 0–21), "
                "to see whether strain matches Recovery.",
        "read": [("Strain follows Recovery", "Matched"), ("Recovery falling, strain still high", "Overreaching")],
        "recovery": "Keeping strain high while Recovery keeps falling risks overtraining; strain staying low while Recovery is high "
                    "means you can train a bit more.",
        "tips": ["Hard sessions on green days, rest or light activity on red days"],
    },
    "training_load": {
        "title": "Training load CTL / ATL / TSB",
        "what": "CTL (fitness) = 42-day weighted average of TRIMP, ATL (fatigue) = the 7-day one, TSB (form) = CTL − ATL.",
        "read": [("TSB > +10", "Fresh"), ("−10 to +10", "Balanced"), ("−30 to −10", "Some fatigue"), ("< −30", "High fatigue")],
        "recovery": "A slowly rising CTL means fitness is building; a TSB that stays very negative means recovery isn't keeping up.",
        "tips": ["Increase weekly load by no more than about 10%", "Plan a deload week every 3–4 weeks"],
    },
    "acwr": {
        "title": "Acute:chronic workload ratio",
        "what": "Average TRIMP of the last 7 days ÷ the average of the last 28 days (ACWR, Gabbett 2016; Apple Training Load also "
                "compares 7 and 28 days).",
        "read": [("0.8–1.3", "Optimal"), ("1.3–1.5", "High"), ("> 1.5", "Too high"), ("< 0.8", "Low")],
        "recovery": "When load is suddenly much higher than usual (> 1.5), the risk of injury and excessive fatigue rises clearly.",
        "tips": ["Back from a break, start at 70–80% of your usual volume"],
    },
    "strain_combined": {
        "title": "Strain (incl. muscle)",
        "what": "An optional definition: cardiovascular TRIMP plus the muscle load of strength training (your median session of the last "
                "90 days ≈ 60 TRIMP), converted to 0–21, similar to how WHOOP counts Strength Trainer in Strain. "
                "It does not replace the main strain.",
        "read": [("0–9", "Light"), ("10–13", "Moderate"), ("14–17", "High"), ("18–21", "Very high")],
        "recovery": "Strength training keeps heart rate low, so cardiovascular strain alone underestimates days like leg day.",
        "tips": ["Don't schedule too many strength days and hard cardio days back to back"],
    },
    "aerobic_min": {
        "title": "Cardio minutes / week", "unit": "min",
        "what": "Minutes at 60–80% of HRmax count ×1, above 80% count ×2 (similar to Fitbit's Active Zone Minutes); "
                "weekly target 150 (WHO recommendation).",
        "read": [("≥ 150", "Target met"), ("75–149", "Halfway"), ("< 75", "Low")],
        "recovery": "Regular moderate cardio raises HRV, lowers resting HR and helps fat loss.",
        "tips": ["30–45 minutes of incline walking 3–4 times a week meets the target"],
    },
    "muscle_load": {
        "title": "Training volume / week",
        "what": "Sum of reps × weight over all working sets of strength training in a week (logged in SparkyFitness).",
        "read": [("Rising steadily", "Progressing"), ("Up and down", "Unsteady")],
        "recovery": "A sudden jump in volume within a week easily pulls Recovery down.",
        "tips": ["Increase weekly volume by no more than about 10%"],
    },
    "steps": {
        "title": "Steps", "unit": "steps",
        "what": "Steps per day (WHOOP shows them too since 5.0).",
        "read": [("≥ 8000", "Good"), ("5000–7999", "Fair"), ("< 5000", "Low")],
        "recovery": "Everyday activity (NEAT) is a big part of fat loss; a sedentary day doesn't necessarily mean higher Recovery.",
        "tips": ["Walk 10–15 minutes after meals; it also helps blood sugar and triglycerides"],
    },
    "calories": {
        "title": "Energy burned",
        "what": "Active energy = exercise and daily activity; total = active + resting (basal metabolism). From Apple Health.",
        "read": [("Against your food target", "Check the deficit")],
        "recovery": "Too big a calorie deficit (> 25% below what you burn) hurts recovery, sleep and strength.",
        "tips": ["Adjust your diet by the weekly average burn, not single days"],
    },
    "hr_recovery": {
        "title": "Heart rate recovery",
        "what": "How far your heart rate drops in the first minute after exercise (Apple's Cardio Recovery). Uses Apple Health's value "
                "first; otherwise computed from raw heart rate around the end of the workout.",
        "read": [("> 20", "Good"), ("13–20", "Fair"), ("≤ 12", "Low")],
        "recovery": "A faster drop means the parasympathetic system takes over sooner and cardiorespiratory fitness is better; "
                    "a value that stays low is a cardiovascular risk signal.",
        "tips": ["Don't take the watch off right after training; stand or walk slowly for 2 minutes"],
    },
    "stress": {
        "title": "Stress",
        "what": "How far your heart rate (and daytime HRV) while awake, not training and not walking deviates from your 14-day calm "
                "baseline, 0–3 (WHOOP Stress Monitor).",
        "read": [("0–1", "Low"), ("1–2", "Medium"), ("2–3", "High")],
        "recovery": "Daytime stress that stays high pulls down that night's HRV and the next day's Recovery.",
        "tips": ["On a high-stress afternoon, go for a 10-minute walk or do a few minutes of slow breathing"],
    },
    "resilience": {
        "title": "Resilience",
        "what": "The balance of your recovery capacity over the last 14 days (Recovery + how well you met your sleep need) minus half "
                "of your daytime stress load, in 5 levels (similar to Oura's Resilience; the levels are our own first version).",
        "read": [("Exceptional / strong", "Handles stress well"), ("Solid", "Normal"), ("Adequate / limited", "Needs a reset")],
        "recovery": "With low resilience, the same training and stress more easily overwhelm your recovery.",
        "tips": ["Resilience falling: protect sleep first, then think about adding training"],
    },
    "body_age": {
        "title": "Body age", "unit": "yrs",
        "what": "A \"body age\" converted from 8 sleep, activity and fitness metrics, computed weekly (WHOOP Age / Healthspan).",
        "read": [("Below your actual age", "Younger"), ("Close", "On par"), ("Above your actual age", "Older")],
        "recovery": "It changes slowly; look at the direction over months.",
        "tips": ["What works best: 7+ hours of sleep, 150 minutes of cardio and two strength sessions a week, and a higher VO₂max"],
    },
    "behavior": {
        "title": "Behaviour impact",
        "what": "For each journal behaviour (alcohol, late meals, sauna…) with at least 5 \"yes\" and 5 \"no\" days in the last 90 days, "
                "the difference in next-day average Recovery (WHOOP's Behavior Insights). A ? means the difference is not yet "
                "certain (few samples or large swings).",
        "read": [("Positive", "Linked to better recovery"), ("Negative", "Linked to worse recovery")],
        "recovery": "Correlation, not causation, but logging consistently shows which habits really hold you back.",
        "tips": ["Spend 10 seconds a day checking in under Journal (or tell your AI); the more days, the more accurate"],
    },
    "hrv_status": {
        "title": "HRV status",
        "what": "Where your 7-day average HRV falls relative to your own baseline band of the previous 60 days (mean ± 1 SD) "
                "(Garmin's HRV Status).",
        "read": [("Balanced", "In the band"), ("High", "Above the band"), ("Low", "Below the band"), ("Poor", "More than 2 SD below")],
        "recovery": "Much steadier than a single night's HRV: a night or two of swings doesn't matter; only when the 7-day average "
                    "leaves the band has fatigue or stress built up.",
        "tips": ["Low: cut back for 2–3 days, sleep enough, rule out alcohol and illness"],
    },
    "training_status": {
        "title": "Training status",
        "what": "Training effect judged from the acute:chronic workload ratio (7 / 28 days), HRV status and the 60-day change in VO₂max "
                "(similar to Garmin Training Status / Polar training load status). The load focus below is the share of 28-day TRIMP "
                "at low aerobic (50–70%), high aerobic (70–90%) and anaerobic (≥ 90% HRmax) intensity.",
        "read": [("Productive", "Load optimal and fitness rising"), ("Maintaining", "Load optimal"), ("Recovery", "Load lower than usual"),
                 ("Strained", "Load optimal but HRV low"), ("Unproductive", "Fitness falling"), ("Overreaching", "Load ratio > 1.5"),
                 ("Detraining", "Training too little for a long time")],
        "recovery": "Overreaching or strained: cut back first. Detraining: build your training volume back up gradually.",
        "tips": ["Low aerobic making up most of the load is normal (the 80/20 rule)",
                 "To raise VO₂max: add one high-intensity interval session a week"],
    },
    "body_battery": {
        "title": "Body battery (estimate)",
        "what": "A rough estimate modelled on Garmin Body Battery: on waking = 20 + 75 × Recovery %, then drained each hour by time "
                "awake, stress and the day's strain. Apple Watch has no continuous daytime HRV, so it is only an estimate.",
        "read": [("≥ 60", "Charged"), ("30–59", "Medium"), ("< 30", "Low")],
        "recovery": "When the battery is already low in the afternoon, hard training in the evening pushes the next day's Recovery down further.",
        "tips": ["Battery low: swap hard training for easy cardio or stretching"],
    },
    "recovery_time": {
        "title": "Recovery time (estimate)", "unit": "h",
        "what": "Modelled on Garmin Recovery Time: from the highest-strain day of the last 4 days, each strain point above 8 needs "
                "5 hours, adjusted for fatigue (TSB) and today's Recovery colour; shows the hours left as of 7 am today.",
        "read": [("0", "Recovered"), ("1–24 h", "Partly recovered"), ("> 24 h", "Still recovering")],
        "recovery": "You can train before you have fully recovered, but avoid the same muscle groups and high intensity.",
        "tips": ["Still recovering: train other body parts or do Zone 2"],
    },
    "chronotype": {
        "title": "Chronotype",
        "what": "The sleep midpoint on free days (waking on Saturday or Sunday) over the last 60 nights, corrected for the sleep "
                "difference between workdays and free days (MSFsc from the Munich ChronoType Questionnaire, MCTQ).",
        "read": [("< 3:00", "Early"), ("3:00–4:29", "Intermediate"), ("4:30–5:59", "Late"), ("≥ 6:00", "Very late")],
        "recovery": "Late chronotypes who force early rising build sleep debt; to move your schedule earlier, shift it gradually "
                    "with morning sunlight and a fixed wake time.",
        "tips": ["Move your wake time only 15–30 minutes earlier per week", "Dim the lights in the evening"],
    },
}
