"""Moved to the openbodyscore package (packages/openbodyscore/src/openbodyscore/hrv.py, issue #115).
This old import path is an alias of that very module, so existing imports, monkeypatching and private names keep working."""
import sys

import openbodyscore.hrv

sys.modules[__name__] = openbodyscore.hrv
