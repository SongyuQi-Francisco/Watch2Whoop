#!/usr/bin/env python3
"""Stream-parse Apple Health export.xml into compact daily CSVs."""
import sys, os, csv, collections, datetime
import xml.etree.ElementTree as ET
src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
SUM = {"HKQuantityTypeIdentifierStepCount","HKQuantityTypeIdentifierActiveEnergyBurned","HKQuantityTypeIdentifierBasalEnergyBurned",
       "HKQuantityTypeIdentifierAppleExerciseTime","HKQuantityTypeIdentifierDistanceWalkingRunning","HKQuantityTypeIdentifierFlightsClimbed",
       "HKQuantityTypeIdentifierAppleStandTime","HKQuantityTypeIdentifierDistanceSwimming","HKQuantityTypeIdentifierSwimmingStrokeCount",
       "HKQuantityTypeIdentifierDietaryWater","HKQuantityTypeIdentifierDietaryEnergyConsumed","HKQuantityTypeIdentifierDietaryProtein",
       "HKQuantityTypeIdentifierTimeInDaylight","HKQuantityTypeIdentifierPhysicalEffort"}
daily = collections.defaultdict(lambda: [0.0,0,float('inf'),float('-inf')])  # (type,day)->sum,n,min,max
srcsum = collections.defaultdict(float)  # (type,day,source)->sum, for de-duplicating cumulative types
units = {}
sleep = open(os.path.join(out,"sleep_segments.csv"),"w",newline=""); sw=csv.writer(sleep); sw.writerow(["start","end","stage","source"])
wk = open(os.path.join(out,"workouts.csv"),"w",newline=""); ww=csv.writer(wk); ww.writerow(["type","start","end","duration_min","energy_kcal","distance_km","avg_hr","max_hr","source"])
counts = collections.Counter()
for ev, el in ET.iterparse(src, events=("end",)):
    tag = el.tag
    if tag == "Record":
        t = el.get("type"); counts[t]+=1
        if t == "HKCategoryTypeIdentifierSleepAnalysis":
            sw.writerow([el.get("startDate"), el.get("endDate"), el.get("value","").replace("HKCategoryValueSleepAnalysis",""), el.get("sourceName")])
        else:
            v = el.get("value")
            try: v = float(v)
            except (TypeError, ValueError): el.clear(); continue
            day = el.get("startDate","")[:10]
            d = daily[(t,day)]; d[0]+=v; d[1]+=1; d[2]=min(d[2],v); d[3]=max(d[3],v)
            if t in SUM: srcsum[(t,day,el.get("sourceName"))]+=v
            units[t] = el.get("unit")
        el.clear()
    elif tag == "Workout":
        stats = {s.get("type"): s for s in el.findall("WorkoutStatistics")}
        hr = stats.get("HKQuantityTypeIdentifierHeartRate")
        en = stats.get("HKQuantityTypeIdentifierActiveEnergyBurned")
        di = stats.get("HKQuantityTypeIdentifierDistanceWalkingRunning")
        if di is None: di = stats.get("HKQuantityTypeIdentifierDistanceSwimming")
        ww.writerow([el.get("workoutActivityType","").replace("HKWorkoutActivityType",""), el.get("startDate"), el.get("endDate"),
                     round(float(el.get("duration") or 0),1), en.get("sum") if en is not None else el.get("totalEnergyBurned"),
                     di.get("sum") if di is not None else el.get("totalDistance"),
                     hr.get("average") if hr is not None else "", hr.get("maximum") if hr is not None else "", el.get("sourceName")])
        el.clear()
sleep.close(); wk.close()
with open(os.path.join(out,"daily_metrics.csv"),"w",newline="") as f:
    best = {}
    for (t,day,src),v in srcsum.items():
        best[(t,day)] = max(best.get((t,day),0), v)   # take the most complete single source (avoids iPhone+Watch double counting)
    w=csv.writer(f); w.writerow(["type","date","value","agg","n","min","max","unit"])
    for (t,day),(s,n,mn,mx) in sorted(daily.items(), key=lambda x:(x[0][0],x[0][1])):
        short=t.replace("HKQuantityTypeIdentifier","")
        if t in SUM:
            v = best.get((t,day), s); u = units.get(t)
            if u == "kJ": v, u = v/4.184, "kcal"
            w.writerow([short,day,round(v,2),"sum",n,"","",u])
        else: w.writerow([short,day,round(s/n,2),"avg",n,round(mn,2),round(mx,2),units.get(t)])
with open(os.path.join(out,"record_counts.csv"),"w",newline="") as f:
    w=csv.writer(f); w.writerow(["type","records"]); [w.writerow([k,v]) for k,v in counts.most_common()]
open(os.path.join(out,"DONE"),"w").write(datetime.datetime.now().isoformat())
