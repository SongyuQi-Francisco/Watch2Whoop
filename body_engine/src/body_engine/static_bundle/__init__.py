"""Static data bundle (issue #248, item 2): the exercise library and other rarely-changing data as one versioned package the app
keeps locally (shipped inside the app, then updated by version number). See build.py (content, versions), api.py (endpoints) and
cli.py (`body-engine static-bundle-pack`). Rollout flag `static_bundle` (endpoints only; the pack command is not gated).
"""
FORMAT = 1                 # layout of the bundle JSON; the app refuses a higher number than it knows
FLAG = "static_bundle"
