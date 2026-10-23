"""Moved to the datalayer framework (datalayer/compact.py, issue #116, docs/datalayer-design.md section 4 phase 2a).
This old import path is an alias of that very module, so existing imports, monkeypatching and private names keep working."""
import sys

from ..datalayer import compact

sys.modules[__name__] = compact
