"""Today page charts (web/static/app.js, FB-2): the y range covers every reading and at least the nearest edge of the reference
band (so a strip of the band always shows), and nothing a chart draws leaves its plot area. chartRange() is pure; it is cut out of app.js between the chart-range markers and run with node
(skipped without node). The clipping is checked on the source: tChart() clips its data layers and the trend card hides overflow."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")


def chart_range(cases: list[tuple[list, dict]]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    m = re.search(r"// chart-range:start.*?\n(.*?)\n\s*// chart-range:end", APP, re.S)
    assert m, "chart-range markers not found in app.js"
    js = m.group(1) + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(([n, o]) => chartRange(n, o))));"
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


CASES = {
    # FB-2: post-workout HR recovery, scale from 0, normal band 20–60, only a few low readings
    "hrr_few_low": ([8, None, None, 12, None, None, None], {"min": 0, "zone": [20, 60]}),
    "below_min": ([-6, 3], {"min": 0, "zone": [20, 60]}),               # a negative reading under the pinned 0
    "one_point": ([30], {}),
    "two_points": ([30, 34], {"zone": [42, 100]}),
    "all_above_band": ([80, 90, 95], {"zone": [20, 60]}),
    "all_below_band": ([1, 2], {"min": 0, "max": 3, "zone": [0, 1]}),
    "same_values": ([50, 50, 50], {}),
    "zero_values": ([0, 0], {}),
    "no_data": ([], {"min": 0, "max": 100}),
    "fixed_scale": ([40, 70], {"min": 0, "max": 110, "zone": [85, 110]}),
    "inside_band": ([62, 66, 70, 72], {"zone": [42, 100]}),            # resilience: a normal week inside its band
    "band_only": ([None, None], {"zone": [20, 60]}),
}


def inside(r, v):
    return r["lo"] <= v <= r["hi"]


def test_range_covers_readings_and_band():
    got = dict(zip(CASES, chart_range(list(CASES.values()))))
    for name, (nums, opts) in CASES.items():
        r = got[name]
        data = [x for x in nums if x is not None]
        assert r["lo"] < r["hi"], name
        for v in data:
            assert inside(r, v), f"{name}: reading {v} outside {r}"
        if opts.get("zone"):   # some of the band is always on the plot
            zlo, zhi = opts["zone"]
            assert r["lo"] <= zhi and zlo <= r["hi"], f"{name}: band {opts['zone']} not on the plot {r}"
    # FB-2: low readings under a band 20–60: the scale starts at the pinned 0 and reaches past the band's lower edge
    assert got["hrr_few_low"]["lo"] == 0 and got["hrr_few_low"]["hi"] > 20
    # readings above the band: its upper edge is on the plot
    assert got["all_above_band"]["lo"] < 60 < got["all_above_band"]["hi"]
    # a band around the readings does not flatten them: the scale follows the readings
    assert got["inside_band"]["hi"] - got["inside_band"]["lo"] < 20
    # pinned sides stay exactly where the card asked, when every reading is inside them
    assert got["fixed_scale"] == {"lo": 0, "hi": 110}
    assert got["no_data"] == {"lo": 0, "hi": 100}
    # a reading below a pinned minimum widens the scale (with padding) instead of being drawn under the axis
    assert got["below_min"]["lo"] < -6
    # unpinned sides get padding, so a point never sits on the edge of the plot
    assert got["one_point"]["lo"] < 30 < got["one_point"]["hi"]
    assert got["band_only"]["lo"] <= 20 and got["band_only"]["hi"] >= 60


def test_charts_are_clipped():
    body = APP[APP.index("function tChart("):APP.index("function bindCharts(")]
    assert 'clip-path="url(#${id}-clip)"' in body and "<clipPath" in body, "tChart must clip its data layers"
    assert "chartRange(nums, opts)" in body, "tChart must take its y range from chartRange"
    # bands and per-day target boxes are clamped to the plot area
    assert body.count("top(sy(") >= 4
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.card\.trend\s*\{\s*overflow:\s*hidden", css), "the trend card must hide overflow"
    lab = APP[APP.index("function labChart("):][:4000]
    assert "inPlot(sy(" in lab, "the lab chart's reference band must stay in the plot area"
