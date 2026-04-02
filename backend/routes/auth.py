# -*- coding: utf-8 -*-
"""Authentication endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Set by api.py during startup
_serializer: URLSafeTimedSerializer | None = None
_credentials: dict = {}
_max_age: int = 86400


def init_auth(config: dict):
    """Initialize auth module with config values."""
    global _serializer, _credentials, _max_age
    auth_cfg = config.get("auth", {})
    _credentials = {
        "username": auth_cfg.get("username", "admin"),
        "password": auth_cfg.get("password", ""),
    }
    _serializer = URLSafeTimedSerializer(auth_cfg.get("secret_key", "change-me"))
    _max_age = int(auth_cfg.get("token_expire_hours", 24)) * 3600


def verify_token(token: str) -> bool:
    """Verify a token. Returns True if valid."""
    try:
        _serializer.loads(token, max_age=_max_age)
        return True
    except (SignatureExpired, BadSignature):
        return False


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest):
    if req.username != _credentials["username"] or req.password != _credentials["password"]:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _serializer.dumps({"u": req.username})
    return {"token": token}


@router.get("/check")
def check():
    """If middleware lets the request through, token is valid."""
    return {"valid": True}
