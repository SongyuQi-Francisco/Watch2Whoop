"""Golden test: every scoring function gives exactly the numbers the code gave before it moved into this package (issue #115)."""
import json

import golden_cases


def test_matches_golden():
    want = json.loads(golden_cases.GOLDEN.read_text())
    got = golden_cases.compute(golden_cases.package_modules())
    for key in want:
        assert got[key] == want[key], key
    assert got == want
