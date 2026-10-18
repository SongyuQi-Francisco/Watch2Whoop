"""HTTP endpoints of the assistant: tool list (for cross-checking). In-Today-page chat POST /v1/assistant/chat is in ai/api.py (multi-provider)."""
from fastapi import APIRouter

from .tools import TOOLS

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])


@router.get("/tools")
def list_tools():
    return [{**t.api_definition(), "writes": t.writes, "scope": t.scope} for t in TOOLS]
