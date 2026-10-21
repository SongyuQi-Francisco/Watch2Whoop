"""Rule output format. Each rule = a pure function (Ctx) -> RuleResult that only looks at data in Ctx, which makes it easy to test."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Level = Literal["info", "warning", "alert"]
Status = Literal["triggered", "not_triggered", "insufficient_data", "error"]


@dataclass
class Reminder:
    rule_key: str
    level: Level
    title: str
    message: str
    subject: str = ""                       # distinguishes several reminders of the same rule on the same day (e.g. exercise ID)
    evidence: dict = field(default_factory=dict)   # supporting data (JSON)
    sticky: bool = False                    # if this subject was "dismissed" before, inherit dismissed
    title_en: str = ""                      # English (issue #42): same facts and numbers; "" = page falls back to title
    message_en: str = ""


@dataclass
class RuleResult:
    rule_key: str
    status: Status
    note: str = ""                          # why it did not fire / why data was insufficient
    reminders: list[Reminder] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"rule_key": self.rule_key, "status": self.status, "note": self.note,
                "reminders": len(self.reminders), **({"details": jsonable(self.details)} if self.details else {})}


def jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if isinstance(x, float):
        return round(x, 3)
    return x
