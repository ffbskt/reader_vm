# -*- coding: utf-8 -*-
"""
Auth. Phase 1 = a single local user, no login — but the shape is final:
every endpoint depends on get_current_user(), and requests may carry
`Authorization: Bearer <token>`. Phase 2 swaps the body of verify_token()
for real JWT (Google OAuth / Telegram Login) without touching endpoints.
"""
import os
from typing import Optional
from fastapi import Header, HTTPException

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-not-for-prod")

LOCAL_USER = {"id": 1, "login": "local", "tier": "free"}

def verify_token(token: str) -> Optional[dict]:
    """Phase 1: any token (or none) maps to the single local user.
    Phase 2: decode+verify JWT here and load the user row."""
    return LOCAL_USER

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if authorization is None:
        return LOCAL_USER                      # Phase 1 convenience
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "expected 'Authorization: Bearer <token>'")
    user = verify_token(parts[1])
    if user is None:
        raise HTTPException(401, "invalid token")
    return user
