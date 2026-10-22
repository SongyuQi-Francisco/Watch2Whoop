"""Golden test: every scoring function gives exactly the numbers the code gave before it moved into this package (issue #115)."""
import json

import golden_cases


def test_matches_golden():
    want = json.loads(golden_cases.GOLDEN.read_text())
    got = golden_cases.compute(golden_cases.package_modules())
    assert golden_cases.mismatch(got, want) is None, golden_cases.mismatch(got, want)


def test_v12_matches_golden():
    """Body age v1.2 (activity domain, issue #118): written when v1.2 was introduced (`python golden_cases.py --write-v12`)."""
    want = json.loads(golden_cases.GOLDEN_V12.read_text())
    got = golden_cases.compute_v12()
    assert golden_cases.mismatch(got, want) is None, golden_cases.mismatch(got, want)
