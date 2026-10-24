"""openGym exercise text in the exercise library (issue #248).

Source: https://github.com/DuarteSantos8/openGym, `catalogue/exercises/*.json`, pinned to one commit. Text only (name, body part,
equipment, muscles, category, description, steps): nothing from `catalogue/media/`, no image or animation, no path or link to
them (those are licensed to openGym only). Licences are kept per entry (THIRD_PARTY_NOTICES.md has the full attribution):
  textSource "exercisedb" -> ExerciseDB v1 by AscendAPI, via hasaneyldrm/exercises-dataset: MIT
  every other entry       -> written for openGym: AGPL-3.0
Files: snapshot.json.gz (built by tools/build_opengym_snapshot.py from a clone at COMMIT), importer.py (the idempotent
`body-engine exercises-import-opengym`), mapping.py (openGym's body-part / equipment / category names -> ours).
"""
COMMIT = "7a3ace53c5e910c2185f22df803726c5838a99ae"
REPO = "https://github.com/DuarteSantos8/openGym"
