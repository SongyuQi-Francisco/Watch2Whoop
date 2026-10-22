"""Apple Push Notification service: token-based authentication over HTTP/2 (issue #175).

Configuration (the deployer provides the key; it is a secret file, never in git or an env var):
  BODY_APNS_KEY_FILE   the .p8 key from Apple Developer → Keys (default secrets/apns_key.p8)
  BODY_APNS_KEY_ID     its 10-character Key ID
  BODY_APNS_TEAM_ID    the Apple Developer Team ID
  BODY_APNS_BUNDLE_ID  the app's Bundle ID (the apns-topic)
Missing any of them = not configured: notify() writes in-app notifications only.

The environment comes from each device registration: sandbox for Xcode / debug builds, production for TestFlight and
the App Store (api.push.apple.com). See Apple's "Sending notification requests to APNs".
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import REPO_ROOT

log = logging.getLogger(__name__)

HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
JWT_MAX_AGE = 50 * 60          # Apple rejects tokens older than 1 hour and refreshing more often than every 20 minutes
TIMEOUT = 10.0
# Responses that mean the device token is no longer valid for this app: remove the device
INVALID_REASONS = {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered", "ExpiredToken"}


@dataclass(frozen=True)
class Config:
    key_pem: bytes
    key_id: str
    team_id: str
    bundle_id: str


@dataclass(frozen=True)
class Result:
    status: int                  # HTTP status from Apple (0 = network error)
    reason: str | None = None    # Apple's "reason" for errors

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def invalid_token(self) -> bool:
        return self.status == 410 or (self.status == 400 and self.reason in INVALID_REASONS)


class Client(Protocol):
    def send(self, token: str, environment: str, payload: dict, headers: dict[str, str]) -> Result: ...


def load_config() -> Config | None:
    key_id, team_id, bundle_id = (os.environ.get(f"BODY_APNS_{k}", "").strip() for k in ("KEY_ID", "TEAM_ID", "BUNDLE_ID"))
    path = Path(os.environ.get("BODY_APNS_KEY_FILE", REPO_ROOT / "secrets" / "apns_key.p8"))
    if not (key_id and team_id and bundle_id) or not path.is_file():
        return None
    return Config(path.read_bytes(), key_id, team_id, bundle_id)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def provider_token(cfg: Config, now: float | None = None) -> str:
    """ES256 JWT {alg, kid} . {iss: team, iat} signed with the .p8 key (raw r||s signature, as JWS wants)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    head = _b64(json.dumps({"alg": "ES256", "kid": cfg.key_id}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iss": cfg.team_id, "iat": int(now or time.time())}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(cfg.key_pem, password=None)
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


class HttpClient:
    """The real client: one HTTP/2 connection per environment, reused; the provider token is cached for 50 minutes."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._jwt: tuple[str, float] | None = None
        self._http: dict[str, object] = {}
        self._lock = threading.Lock()

    def _token(self) -> str:
        if self._jwt is None or time.time() - self._jwt[1] > JWT_MAX_AGE:
            now = time.time()
            self._jwt = (provider_token(self.cfg, now), now)
        return self._jwt[0]

    def _client(self, environment: str):
        import httpx
        if environment not in self._http:
            self._http[environment] = httpx.Client(base_url=HOSTS[environment], http2=True, timeout=TIMEOUT)
        return self._http[environment]

    def send(self, token: str, environment: str, payload: dict, headers: dict[str, str]) -> Result:
        with self._lock:
            try:
                r = self._client(environment).post(
                    f"/3/device/{token}", content=json.dumps(payload, ensure_ascii=False).encode(),
                    headers={"authorization": f"bearer {self._token()}", "apns-topic": self.cfg.bundle_id, **headers})
            except Exception as e:  # noqa: BLE001 - network trouble must never break the caller
                log.warning("APNs %s: %s", environment, e)
                return Result(0, type(e).__name__)
        reason = None
        if r.status_code != 200:
            try:
                reason = r.json().get("reason")
            except ValueError:
                reason = r.text[:80] or None
        return Result(r.status_code, reason)
