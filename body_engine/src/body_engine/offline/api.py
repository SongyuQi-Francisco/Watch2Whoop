"""Writes the Today page can queue while offline that had no page endpoint yet: a day's body weight.

POST /v1/users/{user}/weight {date, weight_kg, client_id?, overwrite?, context_tags?, note?, measured_at?} writes a check-in to the
user's training log (SparkyFitness, the same path as the AI's log_weight) and, when given, its measuring conditions (weightlog/).
Not behind the offline_mode flag: every user can log weight; the flag only decides whether a write waits on the device while offline. Idempotent: the same value on the same day is "unchanged", so a
replayed queue item never writes twice. A different value already on that day is a conflict (409 with the stored value)
unless overwrite=true; the page then asks the user. Only for oneself."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import sparky_client
from ..assistant import sparky_write
from ..i18n import L
from ..records.api import _self, current_user, get_conn
from ..weightlog import service as weight_service

router = APIRouter(prefix="/v1/users/{user}", tags=["offline"])


class WeightIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    weight_kg: float = Field(ge=sparky_write.CHECKIN_RANGES["weight_kg"][0], le=sparky_write.CHECKIN_RANGES["weight_kg"][1])
    client_id: str | None = Field(None, max_length=64)   # the page's queue key; the write is idempotent on (date, value)
    overwrite: bool = False
    context_tags: list[str] | None = Field(None, max_length=10)   # weightlog/params.CONTEXT_TAGS keys; None = leave as it is, [] = clear
    note: str | None = Field(None, max_length=200)
    measured_at: datetime | None = None


@router.post("/weight")
def weight_add(user: str, body: WeightIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        sparky_write.check_date(conn, uid, body.date.isoformat())
    except ValueError:
        raise HTTPException(422, L(f"日期 {body.date} 不能记录（未来或太早）", f"Can't log {body.date} (in the future or too long ago)"))
    try:
        res = weight_service.log_weight(conn, uid, {"date": body.date.isoformat(), "weight_kg": round(body.weight_kg, 1),
                                                    "overwrite": body.overwrite},
                                        context_tags=body.context_tags, note=body.note, measured_at=body.measured_at)
    except sparky_client.SparkyError:
        raise HTTPException(409, L("训练记录还没连接，连上以后才能记体重", "The training log is not connected yet; connect it to log weight"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if res.get("conflicts"):
        existing = res["conflicts"]["weight"]["existing"]
        raise HTTPException(409, {"message": L(f"{body.date} 已经记了 {existing:g} kg", f"{body.date} already has {existing:g} kg"),
                                  "conflict": "weight", "existing_kg": existing})
    conn.commit()
    return {"date": body.date.isoformat(), "weight_kg": round(body.weight_kg, 1),
            "written": bool(res.get("written")), "unchanged": not res.get("written")}
