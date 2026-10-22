"""openbodyscore: recovery, sleep, strain, stress and body-age scores from wearable data. Pure functions, standard library only.

Modules:
- sleep: sleep score (duration, stages, consistency, interruptions), sleep debt, sleep need, sleep performance
- recovery: nightly HRV / resting heart rate, personal baseline, z-score weighted recovery score, illness / temperature / SpO2 flags
- strain: HRmax, Edwards TRIMP, strain 0–21, training load (CTL / ATL / TSB)
- muscle: daily strength-training volume from workout logs
- stress: hourly stress score 0–3 from heart rate / HRV while awake and still
- healthspan: body age and pace of aging from literature hazard ratios
- hrv: RMSSD / SDNN from beat-to-beat times, with artifact filtering
- params, healthspan_params: parameters with their sources

Every scoring function takes an optional keyword `params=`; `<module>.override(NAME=value, ...)` returns a modified
copy of the defaults (stress / hrv keep their parameters in the module itself).
Not affiliated with, endorsed by or derived from WHOOP, Inc. or any wearable maker. Not a medical device.
"""
from . import healthspan, healthspan_params, hrv, muscle, params, recovery, sleep, strain, stress

__version__ = "0.1.0"
__all__ = ["healthspan", "healthspan_params", "hrv", "muscle", "params", "recovery", "sleep", "strain", "stress"]
