# -*- coding: utf-8 -*-
"""
Auth (roadmap 2b.1).

Login: the SPA gets a Google ID token from the Sign-in-with-Google button
and POSTs it to /auth/google; we verify it against Google, find-or-create
the user, and answer with OUR OWN JWT (HS256, 30 days). Every request then
carries `Authorization: Bearer <jwt>`.

The owner rule: the first Google login whose email == OWNER_EMAIL claims
user id 1 — the account that owns all pre-auth data.

Transition mode: requests WITHOUT a token still map to user 1 so the live
site keeps working until quotas land (2b.2 flips REQUIRE_AUTH on).
"""
import base64, hashlib, hmac, json, os, time
from typing import Optional

from fastapi import Header, HTTPException

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-not-for-prod")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "ffbskt@gmail.com")
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "0") == "1"

def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")

def mint_jwt(user_id: int, days: int = 30) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"sub": str(user_id),
                               "exp": int(time.time()) + days * 86400}).encode())
    signing = header + b"." + payload
    sig = hmac.new(JWT_SECRET.encode(), signing, hashlib.sha256).digest()
    return (signing + b"." + _b64(sig)).decode()

def verify_jwt(token: str) -> Optional[int]:
    """Returns the user id, or None for anything invalid/expired."""
    try:
        h, p, s = token.split(".")
        signing = f"{h}.{p}".encode()
        want = hmac.new(JWT_SECRET.encode(), signing, hashlib.sha256).digest()
        got = base64.urlsafe_b64decode(s + "==")
        if not hmac.compare_digest(want, got):
            return None
        payload = json.loads(base64.urlsafe_b64decode(p + "=="))
        if payload.get("exp", 0) < time.time():
            return None
        return int(payload["sub"])
    except Exception:
        return None

def verify_google_id_token(id_tok: str) -> dict:
    """Signature + audience check against Google. Raises ValueError."""
    if not GOOGLE_CLIENT_ID:
        raise ValueError("GOOGLE_CLIENT_ID not configured on the server")
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token as g_id_token
    return g_id_token.verify_oauth2_token(
        id_tok, g_requests.Request(), GOOGLE_CLIENT_ID)

def login_google(id_tok: str) -> dict:
    """Verified Google login -> {token, user}. Owner email claims user 1."""
    from api import db
    info = verify_google_id_token(id_tok)
    sub = info["sub"]
    email = info.get("email", "")
    name = info.get("name", "")
    user = db.user_by_google_sub(sub)
    if user is None:
        owner = db.get_user(1)
        if email and email.lower() == OWNER_EMAIL.lower() \
                and owner and not owner.get("google_sub"):
            user = db.attach_google(1, sub, email, name)
        else:
            user = db.create_google_user(sub, email, name)
    return {"token": mint_jwt(user["id"]), "user": public_user(user)}

TELEGRAM_BOT_SECRET = os.environ.get("TELEGRAM_BOT_SECRET", "")
OWNER_TG_ID = int(os.environ.get("OWNER_TG_ID", "0") or 0)

def login_telegram(tg_id: int, name: str, bot_secret: str) -> dict:
    """The bot (and only the bot — shared secret) exchanges a chat's
    tg_id for a session JWT. Owner tg_id claims user 1."""
    from api import db
    if not TELEGRAM_BOT_SECRET or bot_secret != TELEGRAM_BOT_SECRET:
        raise ValueError("bad bot secret")
    user = db.user_by_tg_id(tg_id)
    if user is None:
        owner = db.get_user(1)
        if OWNER_TG_ID and tg_id == OWNER_TG_ID \
                and owner and not owner.get("tg_id"):
            user = db.attach_tg(1, tg_id, name)
        else:
            user = db.create_tg_user(tg_id, name)
    return {"token": mint_jwt(user["id"]), "user": public_user(user)}

def public_user(user: dict) -> dict:
    return {"id": user["id"], "email": user.get("email"),
            "name": user.get("name"), "tier": user.get("tier", "free")}

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    from api import db
    if authorization is None:
        if REQUIRE_AUTH:
            raise HTTPException(401, "login required")
        return db.get_user(1)              # transition mode: legacy user
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "expected 'Authorization: Bearer <token>'")
    uid = verify_jwt(parts[1])
    user = db.get_user(uid) if uid is not None else None
    if user is None:
        raise HTTPException(401, "invalid or expired token")
    return user
