"""Self-service sign-up endpoints (docs/self-signup-plan.md).

Sign-in (no auth):
  POST /v1/auth/email/start   {email, lang}                       → mail a 6-digit code (same answer whether or not an account exists)
  POST /v1/auth/email/verify  {email, code, client, device}       → session
  POST /v1/auth/apple         {identity_token, nonce, client, device, given_name, family_name} → session
  "session": client=web sets the Today page cookie; client=app returns a device API token (Bearer) instead.
Account (Bearer or cookie; also for `pending` accounts):
  GET    /v1/account                       status, identities, provisioning
  POST   /v1/account/redeem-invite {code}  pending → active, provisioning starts in the background
  POST   /v1/account/ow-invitation         fresh Open Wearables invitation code for this user's sync account (active only)
  POST   /v1/account/web-login-code        one-time Today page login code (the app logs its WebView in with it)
  POST   /v1/account/logout                revoke the Bearer token used (app sign-out)
  DELETE /v1/account {confirm: "DELETE"}   delete the account and all its data
  GET    /v1/users/{user}/invite-code      the user's own referral code (created on first call) + how many joined; 404 unless
                                           BODY_REFERRALS_ENABLED (the Me page entry hides itself: /v1/auth/methods → referrals)
  POST /v1/account/redeem-invite takes admin invite codes and users' referral codes alike (same 10-character format).
A `pending` account gets 403 {"detail": "account_pending"} everywhere else (api.current_user → gate()).

Limits: verification / redemption failures count against the shared per-IP login failure limit (web/login_limit.py);
code e-mails are limited per address (EMAIL_SENDS_PER_ADDRESS per hour, one per RESEND_SECONDS) and per IP.
"""
from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..web import activation, login_codes, login_limit, session
from . import delete as account_delete, invites, mailer, params, provision, signin

log = logging.getLogger(__name__)
router = APIRouter(tags=["account"])

EMAIL_SENDS_PER_ADDRESS = 5        # per hour
RESEND_SECONDS = 60
EMAIL_SENDS_PER_IP = 20            # per hour
WEB_LOGIN_CODE_MINUTES = 10
_per_address = login_limit.Limiter(EMAIL_SENDS_PER_ADDRESS, 3600)
_resend = login_limit.Limiter(1, RESEND_SECONDS)
_per_ip = login_limit.Limiter(EMAIL_SENDS_PER_IP, 3600)

# Paths a pending account may use (everything else answers 403 account_pending).
PENDING_OK = ("/v1/account", "/v1/web/me", "/v1/me", "/v1/web/logout")


def gate(user: dict, path: str) -> None:
    """Called for every authenticated request (api.current_user)."""
    status = user.get("status") or "active"
    if status == "active":
        return
    if status == "disabled":
        raise HTTPException(403, "account_disabled")
    if not any(path == p or path.startswith(p + "/") for p in PENDING_OK):
        raise HTTPException(403, "account_pending")


def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn)) -> dict:
    from .. import api
    return api.current_user(request, conn, request.headers.get("authorization"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _check_ip(request: Request) -> str:
    ip = _ip(request)
    if wait := login_limit.retry_after(ip):
        raise HTTPException(429, "失败次数太多，请稍后再试", headers={"Retry-After": str(wait)})
    return ip


def me_dict(user: dict) -> dict:
    return {k: user.get(k) for k in ("id", "display_name", "role", "timezone", "status")}


def _session_response(conn, request: Request, response: Response, user: dict, new_user: bool, client: str,
                      device: str | None) -> dict:
    token = signin.device_token(conn, user["id"], client, device)
    out = {"status": user["status"], "new_user": new_user, "me": me_dict(user)}
    if client == "app":
        out["token"] = token
    else:
        session_id, _ = session.login(conn, token, request.headers.get("user-agent"))
        session.set_cookie(response, session_id, request)
    return out


@router.get("/v1/auth/methods")
def methods():
    """Which sign-in methods this server offers (the login pages hide the others)."""
    return {"email": mailer.configured(), "apple": bool(signin.apple_audiences()), "referrals": params.referrals_enabled()}


# ---------------------------------------------------------------------------------------------------- e-mail

class EmailStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    lang: str = Field("zh", pattern="^(zh|en)$")


@router.post("/v1/auth/email/start")
def email_start(body: EmailStartIn, request: Request, conn=Depends(get_conn)):
    """Send a sign-in code. 429 when this address / IP asked too often; 503 when no mail sender is configured."""
    if not mailer.configured():
        raise HTTPException(503, "服务器还没配置发信，暂时不能用邮箱登录")
    email = signin.normalize_email(body.email)
    if email is None:
        raise HTTPException(400, "邮箱格式不对")
    ip = _ip(request)
    for limiter, key in ((_per_ip, ip), (_per_address, email), (_resend, email)):
        if wait := limiter.retry_after(key):
            raise HTTPException(429, "发送太频繁，请稍后再试", headers={"Retry-After": str(wait)})
    code = signin.new_email_code(conn, email)
    conn.commit()
    for limiter, key in ((_per_ip, ip), (_per_address, email), (_resend, email)):
        limiter.record(key)
    subject, text = mailer.code_message(code, signin.CODE_MINUTES, body.lang)
    try:
        mailer.send(email, subject, text)
    except Exception as e:  # noqa: BLE001
        log.error("sign-in code e-mail failed: %s", e)
        raise HTTPException(502, "邮件发送失败，请稍后再试")
    return {"sent": True, "expires_in": signin.CODE_MINUTES * 60}


class EmailVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=12)
    client: str = Field("web", pattern="^(web|app)$")
    device: str | None = Field(None, max_length=60)


@router.post("/v1/auth/email/verify")
def email_verify(body: EmailVerifyIn, request: Request, response: Response, conn=Depends(get_conn)):
    ip = _check_ip(request)
    email = signin.normalize_email(body.email)
    ok = email is not None and signin.check_email_code(conn, email, body.code)
    conn.commit()
    if not ok:
        login_limit.record_failure(ip)
        raise HTTPException(401, "验证码不对或已过期")
    try:
        user, new_user = signin.find_or_create_user(conn, "email", email, email, True)
    except signin.SignInError:
        raise HTTPException(403, "account_disabled")
    return _session_response(conn, request, response, user, new_user, body.client, body.device)


# ---------------------------------------------------------------------------------------------------- Apple

class AppleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_token: str = Field(min_length=20, max_length=8000)
    nonce: str | None = Field(None, max_length=200)
    client: str = Field("app", pattern="^(web|app)$")
    device: str | None = Field(None, max_length=60)
    given_name: str | None = Field(None, max_length=60)
    family_name: str | None = Field(None, max_length=60)


@router.post("/v1/auth/apple")
def apple(body: AppleIn, request: Request, response: Response, conn=Depends(get_conn)):
    """Sign in with Apple (native iOS): verify the identity token against Apple's keys, audience = BODY_APPLE_AUDIENCES."""
    audiences = signin.apple_audiences()
    if not audiences:
        raise HTTPException(503, "服务器还没配置「通过 Apple 登录」")
    ip = _check_ip(request)
    try:
        claims = signin.verify_apple_token(body.identity_token, audiences, body.nonce)
    except signin.SignInError as e:
        log.info("apple sign-in rejected: %s", e)
        login_limit.record_failure(ip)
        raise HTTPException(401, "Apple 登录无效，请重试")
    email, verified = signin.apple_email(claims)
    name = " ".join(x for x in (body.given_name, body.family_name) if x) or None
    try:
        user, new_user = signin.find_or_create_user(conn, "apple", claims["sub"], email, verified, name)
    except signin.SignInError:
        raise HTTPException(403, "account_disabled")
    return _session_response(conn, request, response, user, new_user, body.client, body.device)


# ---------------------------------------------------------------------------------------------------- account

def account_info(conn, user: dict) -> dict:
    ids = conn.execute("SELECT provider, email FROM user_identities WHERE user_id = %s ORDER BY id", (user["id"],)).fetchall()
    return {**me_dict(user), "identities": ids, "invite_required": user.get("status") == "pending",
            "provisioning": provision.status(conn, user["id"])}


@router.get("/v1/account")
def get_account(conn=Depends(get_conn), viewer=Depends(current_user)):
    return account_info(conn, viewer)


class RedeemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=40)


def _provision_in_background(user_id: int) -> None:
    from .. import api
    try:
        with api.pool.connection() as conn:
            provision.run(conn, user_id)
    except Exception as e:  # noqa: BLE001  the hourly provision-run retries
        log.warning("background provisioning of user %s failed: %s", user_id, e)


def start_provisioning(user_id: int) -> None:
    """Provision in a background thread so the app does not wait on OW / SparkyFitness (tests replace this)."""
    threading.Thread(target=_provision_in_background, args=(user_id,), daemon=True).start()


@router.post("/v1/account/redeem-invite")
def redeem_invite(body: RedeemIn, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    ip = _check_ip(request)
    if not invites.redeem(conn, viewer["id"], body.code):
        conn.rollback()
        login_limit.record_failure(ip)
        raise HTTPException(401, "激活码无效、已用完或已过期")
    conn.commit()
    if provision.status(conn, viewer["id"])["state"] != "done":
        start_provisioning(viewer["id"])
    user = conn.execute("SELECT * FROM users WHERE id = %s", (viewer["id"],)).fetchone()
    return {"status": user["status"], "provisioning": provision.status(conn, viewer["id"])}


@router.get("/v1/users/{user}/invite-code")
def my_invite_code(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Referral groundwork: the user's own code for friends (redeeming it records the inviter and a pending reward)."""
    if not params.referrals_enabled():
        raise HTTPException(404, "referrals_disabled")
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, "邀请码只能看自己的")
    try:
        res = invites.referral_code(conn, viewer["id"])
    except ValueError as e:
        conn.rollback()
        raise HTTPException(403, "referral_revoked" if str(e) == "revoked" else "account_pending")
    conn.commit()
    return {**res, "link": f"/app/?invite={res['code']}", "daily_limit": params.REFERRAL_DAILY_REDEEMS}


@router.post("/v1/account/ow-invitation")
def ow_invitation(conn=Depends(get_conn), viewer=Depends(current_user)):
    """A fresh Open Wearables invitation code for this user's own sync account; the app redeems it silently."""
    import os
    if viewer.get("status") != "active":
        raise HTTPException(403, "account_pending")
    row = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (viewer["id"],)).fetchone()
    if not row["ow_user_id"]:
        raise HTTPException(409, "provisioning")
    signing = activation.ow_signing()
    if signing is None:
        raise HTTPException(503, "ow_signing_not_configured")
    try:
        code = activation.mint_ow_invitation(row["ow_user_id"], signing)
    except Exception as e:  # noqa: BLE001
        log.warning("ow-invitation for user %s failed: %s", viewer["id"], e)
        raise HTTPException(503, "ow_unavailable")
    return {"code": code, "ow_url": (os.environ.get("BODY_OW_PUBLIC_URL") or "").rstrip("/") or None}


@router.post("/v1/account/web-login-code")
def web_login_code(conn=Depends(get_conn), viewer=Depends(current_user)):
    """A one-time login code (10 minutes) so the app can sign its embedded Today page in: open /app/#login=<code>."""
    hours = 1
    code = login_codes.create(conn, viewer["id"], "signin-webview", hours=hours)
    conn.execute("UPDATE web_login_codes SET expires_at = created_at + make_interval(mins => %s) WHERE code_sha256 = %s",
                 (WEB_LOGIN_CODE_MINUTES, login_codes._sha(code)))
    return {"code": code, "expires_in": WEB_LOGIN_CODE_MINUTES * 60}


@router.post("/v1/account/logout", status_code=204)
def app_logout(request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    authz = request.headers.get("authorization") or ""
    if authz.startswith("Bearer "):
        conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE token_sha256 = %s AND revoked_at IS NULL",
                     (auth.hash_token(authz.removeprefix("Bearer ").strip()),))
    return Response(status_code=204)


class DeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


@router.delete("/v1/account")
def delete_account(body: DeleteIn, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Delete this account and all its data. Needs {"confirm": "DELETE"}; the deployer account cannot be deleted here."""
    if body.confirm != "DELETE":
        raise HTTPException(400, "要确认删除：confirm = DELETE")
    try:
        res = account_delete.delete_account(conn, viewer["id"])
    except account_delete.CannotDelete as e:
        raise HTTPException(403, str(e))
    from fastapi.responses import JSONResponse
    out = JSONResponse({"deleted": True, "leftovers": len(res["leftovers"])})
    session.clear_cookie(out, request)
    return out
