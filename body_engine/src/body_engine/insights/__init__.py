"""Analytics matching WHOOP (docs/system-design.md §7 "body age and other analytics", docs/whoop-feature-map.md).
- healthspan*: body age / pace of aging (weekly, table healthspan_weekly)
- stress: stress monitor (daily, table stress_daily)
- coach: today's strain target, sleep coach, health monitor (computed on demand)
- sleep_detail: sleep regularity (SRI), efficiency, latency; fitness: heart-rate zones, VO₂max; reports: weekly / monthly reports
- views: read functions (shared by the API and butler tools); api: HTTP routes; tools: butler tools; jobs: the part run alongside compute-scores
"""
