"""Raw Apple Watch data beyond what Open Wearables / the Shortcut sync (docs/raw-data-plan.md).

- hrv.py: beat-to-beat intervals of each Apple HRV reading (HKHeartbeatSeriesSample) -> RMSSD / SDNN, with artifact filtering
- ecg.py: ECG recordings (HKElectrocardiogram; electrocardiograms/*.csv in the Health export) -> int16 waveform + R peaks
- export.py: pulls both out of an export.zip / export.xml (also called by importers/apple_zip.py)
- store.py: heartbeat_series / ecg_recordings (migration 150), per user
- api.py: GET /v1/users/{user}/ecg, /ecg/{id}, /heartbeats (scopes: ecg, scores)
- compare.py: SDNN vs RMSSD recovery report (run before switching scores/params.HRV_KIND)
"""
