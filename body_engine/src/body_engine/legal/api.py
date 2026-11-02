"""Public legal pages, no sign-in needed (App Store Connect and the app link to them):

  GET /legal/privacy?lang=zh|en   the privacy policy (HTML)
  GET /legal/terms?lang=zh|en     the terms of use (HTML)
  GET /v1/legal                   {"privacy": {"path", "version"}, "terms": {…}, "min_age"} for clients

Without ?lang the browser's Accept-Language picks (Chinese → zh, anything else → en). Texts and settings: legal/pages.py.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import pages

router = APIRouter(tags=["legal"])


@router.get("/legal/{doc}", response_class=HTMLResponse, include_in_schema=False)
def legal_page(doc: str, request: Request, lang: str | None = None):
    if doc not in pages.DOCS:
        raise HTTPException(404)
    chosen = pages.pick_lang(lang, request.headers.get("accept-language"))
    return HTMLResponse(pages.page(doc, chosen), headers={"Cache-Control": "public, max-age=300",
                                                          "Content-Language": "zh-Hans" if chosen == "zh" else "en"})


@router.get("/v1/legal")
def legal_index():
    return {**{d: {"path": f"/legal/{d}", "version": pages.version(d)} for d in pages.DOCS}, "min_age": pages.min_age()}
