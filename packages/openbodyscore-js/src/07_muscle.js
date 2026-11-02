/* muscle: daily strength-training volume from workout records. Port of openbodyscore/muscle.py.

- muscle_load = Σ reps × weight (kg) over the working sets of all strength exercises that day (a relative measure for the
  same person over time); warm-up sets excluded; bodyweight sets count toward set count but not volume.
- With RPE and duration, session RPE load = mean RPE × minutes (Foster) goes to srpe (a different unit, not added).
- No workout record that day -> no entry (no record ≠ no training).
Sessions have the shape of body_engine sparky_client.training_log: {date, name, total_duration_minutes, exercises: [{category,
primary_muscles, duration_minutes, sets: [{type, reps, weight_kg, duration_s, rpe}]}]}.
*/
OBS.muscle = (function (U) {
  "use strict";
  var WARMUP = { "warm-up": 1, "warmup": 1, "warm up": 1, "热身": 1 };

  /** -> {date: {volume_kg, working_sets, bodyweight_sets, srpe, sessions, sets_by_muscle}} (dates in first-seen order). */
  function daily_muscle_load(sessions) {
    var days = {};
    sessions.forEach(function (s) {
      var d = s.date.slice(0, 10);
      if (!days[d]) days[d] = { volume_kg: 0.0, working_sets: 0, bodyweight_sets: 0, srpe: null, sessions: [], sets_by_muscle: {} };
      var day = days[d];
      day.sessions.push(s.name === undefined ? null : s.name);
      var rpes = [], minutes = s.total_duration_minutes || 0, ex_minutes = 0;
      (s.exercises || []).forEach(function (e) {
        ex_minutes += e.duration_minutes || 0;
        var sets = e.sets.filter(function (x) { return !WARMUP[(x.type || "").toLowerCase()]; });
        sets.forEach(function (x) { if (x.rpe !== null && x.rpe !== undefined) rpes.push(x.rpe); });
        if ((e.category || "strength") !== "strength") return;
        sets.forEach(function (x) {
          if ((x.reps === null || x.reps === undefined) && (x.duration_s === null || x.duration_s === undefined)) return;
          day.working_sets += 1;
          (e.primary_muscles || []).forEach(function (m) { day.sets_by_muscle[m] = (day.sets_by_muscle[m] || 0) + 1; });
          if (x.weight_kg && x.reps) day.volume_kg += x.reps * x.weight_kg;
          else day.bodyweight_sets += 1;
        });
      });
      minutes = minutes || ex_minutes;
      if (rpes.length && minutes) day.srpe = U.round((day.srpe || 0) + U.pySum(rpes) / rpes.length * minutes, 1);
    });
    Object.keys(days).forEach(function (d) { days[d].volume_kg = U.round(days[d].volume_kg, 1); });
    return days;
  }

  return { daily_muscle_load: daily_muscle_load, WARMUP: Object.keys(WARMUP) };
})(OBS.util);
