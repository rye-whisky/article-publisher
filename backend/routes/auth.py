# -*- coding: utf-8 -*-
"""Authentication endpoints."""

import contextvars
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Set by api.py during startup
_serializer: URLSafeTimedSerializer | None = None
_credentials: dict = {}
_max_age: int = 86400

# Context var to store current username (set by middleware)
_current_username: contextvars.ContextVar[str | None] = contextvars.ContextVar("_current_username", default=None)
_current_role: contextvars.ContextVar[str | None] = contextvars.ContextVar("_current_role", default=None)

# Reference to database (set during init)
_db = None


def require_admin():
    """FastAPI dependency: raises 403 if current user is a guest."""
    role = _current_role.get()
    if role == "guest":
        raise HTTPException(status_code=403, detail="访客无操作权限")
    return True


def init_auth(config: dict, database=None):
    """Initialize auth module with config values."""
    global _serializer, _credentials, _max_age, _db
    auth_cfg = config.get("auth", {})
    _credentials = {
        "username": auth_cfg.get("username", "admin"),
        "password": auth_cfg.get("password", ""),
    }
    _serializer = URLSafeTimedSerializer(auth_cfg.get("secret_key", "change-me"))
    _max_age = int(auth_cfg.get("token_expire_hours", 24)) * 3600

    if database:
        _db = database
        # Seed default user from config if no users exist
        database.seed_user(_credentials["username"], _credentials["password"])
        # Seed guest user
        database.seed_guest_user()


def verify_token(token: str) -> dict | None:
    """Verify a token. Returns {username, role} if valid, None otherwise."""
    try:
        data = _serializer.loads(token, max_age=_max_age)
        username = data.get("u")
        if not username:
            return None
        # Get role from token, fallback to DB lookup, then default admin
        role = data.get("r")
        if not role and _db:
            user = _db.get_user_by_username(username)
            if user:
                role = user.get("role", "admin")
        return {"username": username, "role": role or "admin"}
    except (SignatureExpired, BadSignature):
        return None


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest):
    # Check database first, then fallback to config credentials
    authenticated = False
    role = "admin"  # default role
    if _db:
        authenticated = _db.verify_user_password(req.username, req.password)
        if authenticated:
            user = _db.get_user_by_username(req.username)
            if user:
                role = user.get("role", "admin")
    if not authenticated:
        # Fallback to config.yaml credentials
        authenticated = (req.username == _credentials["username"] and req.password == _credentials["password"])

    if not authenticated:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = _serializer.dumps({"u": req.username, "r": role})
    return {"token": token, "role": role, "username": req.username}


@router.get("/check")
def check():
    """If middleware lets the request through, token is valid."""
    return {"valid": True}
