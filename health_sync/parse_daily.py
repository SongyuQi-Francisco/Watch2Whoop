#!/usr/bin/env python3
"""Merge iPhone-shortcut uploads (health_data/archive/health_*.txt, rows: Type|Start|End|Value|Unit|Source)
into health_data/processed/daily_shortcut.csv and sleep_shortcut.csv (deduplicated across uploads)."""
import os, glob, csv, collections, datetime as dt
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "health_data")
rows = {}
for f in sorted(glob.glob(os.path.join(BASE, "archive", "health_*.txt"))):
    for line in open(f, encoding="utf-8", errors="ignore"):
        p = line.rstrip("\n").split("|")
        if len(p) < 6 or not p[1]: continue
        rows[(p[0], p[1], p[2], p[3])] = p          # later uploads overwrite identical samples
SUM = {"Steps", "Active Calories", "Resting Calories", "Exercise Time"}
AVG = {"Heart Rate Variability", "Resting Heart Rate", "Oxygen Saturation", "Respiratory Rate",
       "VO2 Max", "Walking Heart Rate Average", "Weight", "Wrist Temperature", "Body Mass"}
daily = collections.defaultdict(list); sleep = []
for t, s, e, v, u, src in rows.values():
    if t == "Sleep":
        sleep.append((s, e, v)); continue
    if t not in SUM | AVG: continue                  # hourly-summed Heart Rate is skipped (not an average)
    try: x = float(v)
    except ValueError: continue
    if u == "kJ": x, u = x / 4.184, "kcal"
    daily[(s[:10], t, u)].append(x)
out = os.path.join(BASE, "processed"); os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "daily_shortcut.csv"), "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["date", "metric", "value", "agg", "n", "unit"])
    for (d, t, u), xs in sorted(daily.items()):
        agg = "sum" if t in SUM else "avg"
        w.writerow([d, t, round(sum(xs) if agg == "sum" else sum(xs) / len(xs), 2), agg, len(xs), u])
# sleep: group segments into sessions (gap > 60 min), attribute to wake-up day
segs = sorted((dt.datetime.fromisoformat(s), dt.datetime.fromisoformat(e), st) for s, e, st in sleep)
sessions, cur = [], []
for a in segs:
    if cur and (a[0] - cur[-1][1]).total_seconds() > 3600: sessions.append(cur); cur = []
    cur.append(a)
if cur: sessions.append(cur)
with open(os.path.join(out, "sleep_shortcut.csv"), "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["wake_day", "start", "end", "asleep_h", "deep_min", "rem_min", "core_min", "awake_min"])
    for ss in sessions:
        m = collections.Counter()
        for a, b, st in ss: m[st] += (b - a).total_seconds() / 60
        asleep = m["Core"] + m["Deep"] + m["REM"] + m["Asleep"]
        if asleep < 15: continue
        w.writerow([(ss[-1][1] - dt.timedelta(hours=4)).date(), ss[0][0].strftime("%m-%d %H:%M"), ss[-1][1].strftime("%m-%d %H:%M"),
                    round(asleep / 60, 2), round(m["Deep"]), round(m["REM"]), round(m["Core"]), round(m["Awake"])])
print("samples", len(rows), "| daily rows", len(daily), "| sleep sessions", len(sessions))
