"""Minimal client for the Open Wearables (OW) API: standard library only, API key read from a secrets file."""
import json
import urllib.parse
import urllib.request
from collections.abc import Iterator

from .config import settings


class OWError(RuntimeError):
    pass


class OWClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 120):
        self.base_url = (base_url or settings.ow_url).rstrip("/") + "/api/v1"
        self.api_key = api_key or settings.ow_api_key
        if not self.api_key:
            raise OWError("找不到 OW API key：先运行 deploy/open-wearables/bootstrap.sh（写入 secrets/open_wearables_api_key）")
        self.timeout = timeout

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "X-Open-Wearables-API-Key": self.api_key, "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise OWError(f"{method} {path} → {e.code}: {e.read()[:300].decode(errors='replace')}") from e

    def get(self, path: str, **params):
        return self.request("GET", path, params)

    def paginate(self, path: str, **params) -> Iterator[dict]:
        """Iterate OW's cursor pagination (response {data, pagination: {next_cursor, has_more}})."""
        cursor = None
        while True:
            page = self.get(path, **params, cursor=cursor)
            yield from page["data"]
            pg = page.get("pagination") or {}
            cursor = pg.get("next_cursor")
            if not (pg.get("has_more") and cursor):
                return

    def create_user(self, first_name: str, last_name: str | None = None) -> dict:
        return self.request("POST", "/users", body={"first_name": first_name, "last_name": last_name})

    def get_user(self, ow_user_id: str) -> dict:
        return self.get(f"/users/{ow_user_id}")
