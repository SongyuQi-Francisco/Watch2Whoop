"""Body age and pace of aging: the formulas moved to the openbodyscore package (packages/openbodyscore/src/openbodyscore/healthspan.py,
issue #115) and are re-exported here so existing imports keep working. tip() stays here: it picks a product text from TIPS."""
from openbodyscore.healthspan import *  # noqa: F401,F403
from openbodyscore.healthspan import _by_age, _rel, self_report_hours  # noqa: F401

from . import healthspan_params as H


def tip(key: str, years: float, value: float | None = None, goal_kg: float | None = None) -> str:
    """goal_kg: the user's own goal weight (profile), only for body_comp and only when it is below their current weight."""
    if key == "sleep_duration":
        if abs(years) < 0.25 or value is None:
            return H.TIPS["sleep_ok"]
        return H.TIPS["sleep_short"] if self_report_hours(value) < H.SLEEP_NADIR_H else H.TIPS["sleep_long"]
    good, bad = H.TIPS[key]
    if key == "body_comp" and years > 0 and goal_kg:
        return H.TIPS["body_comp_goal"].format(kg=f"{goal_kg:g}")
    return good if years <= 0 else bad
