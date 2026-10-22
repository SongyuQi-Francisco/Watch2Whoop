"""The 记录 segment control (日记 | 训练 | 饮食) is lit from one place, recCur() in app.js.

Bug: with the `records_v2` + `friends` flags, tapping 饮食 showed the food page but the segment stayed on 训练 (recSeg() had
the lit segment hardwired to 训练 under records_v2, and the food page is its own tab under the same segment). recCur() is
run in node (skipped without node) over every flag combination, tab and remembered segment.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static" / "app.js").read_text(encoding="utf-8")


def lit(flags: list[str], tab: str, rec_sub: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    start = APP.index("const recCur = () => {")
    src = APP[start:APP.index("const recSeg = () => {", start)]
    js = f"""
      const S = {{ tab: {json.dumps(tab)}, recSub: {json.dumps(rec_sub)}, flags: {json.dumps(flags)} }};
      const friendsOn = () => S.flags.includes("friends");
      const recordsV2 = () => S.flags.includes("records_v2");
      {src}
      console.log(JSON.stringify(recCur()));"""
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_v2_with_friends_follows_the_food_tab():
    for sub in ("training", "food", "journal"):
        assert lit(["records_v2", "friends"], "food", sub) == "food"       # 饮食 shown -> 饮食 lit, whatever was remembered
    assert lit(["records_v2", "friends"], "record", "training") == "training"
    assert lit(["records_v2", "friends"], "record", "journal") == "training"  # no 日记 segment under records_v2


def test_without_v2_each_segment_is_lit_for_its_page():
    assert lit(["friends"], "food", "training") == "food"
    assert lit(["friends"], "record", "journal") == "journal"
    assert lit(["friends"], "record", "training") == "training"
    assert lit(["friends"], "record", "food") == "food"


def test_without_friends_food_is_not_a_segment():
    assert lit([], "record", "food") == "journal"
    assert lit(["records_v2"], "record", "food") == "training"
    assert lit([], "record", "training") == "training"


def test_every_entry_point_draws_the_segment_through_recCur():
    assert "const cur = recCur();" in APP
    assert "aria-pressed=\"${cur === k}\"" in APP
