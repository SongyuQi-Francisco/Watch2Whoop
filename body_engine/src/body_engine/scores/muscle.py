"""Moved to the openbodyscore package (packages/openbodyscore/src/openbodyscore/muscle.py, issue #115).
This old import path is an alias of that very module, so existing imports, monkeypatching and private names keep working."""
import sys

import openbodyscore.muscle

sys.modules[__name__] = openbodyscore.muscle
