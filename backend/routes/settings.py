# -*- coding: utf-8 -*-
"""Settings and user profile endpoints."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Depends
from routes.auth import require_admin
from pydantic import BaseModel

log = logging.getLogger("pipeline")

router = APIRouter(prefix="/api", tags=["settings"])

# Set during startup by api.py
_db = None


def init_settings_routes(database):
    global _db
    _db = database


# -- Settings --


class SettingsUpdate(BaseModel):
    settings: dict


@router.get("/settings")
def get_settings():
    """Get all settings (API key masked)."""
    raw = _db.get_all_settings()
    result = {}
    for k, v in raw.items():
        if "api_key" in k and v and len(v) > 4:
            result[k] = "*" * (len(v) - 4) + v[-4:]
        else:
            result[k] = v
    return result


@router.put("/settings")
def update_settings(req: SettingsUpdate, _admin=Depends(require_admin)):
    """Batch update settings."""
    _db.set_settings_batch(req.settings)
    return {"ok": True}


@router.post("/settings/test-llm")
async def test_llm(_admin=Depends(require_admin)):
    """Test LLM connection using stored settings."""
    import asyncio

    api_key = _db.get_setting("llm_api_key") or ""
    api_url = _db.get_setting("llm_api_url") or ""
    model = _db.get_setting("llm_model") or ""

    if not api_url or not api_key or not model:
        raise HTTPException(status_code=400, detail="请先完整填写 API URL、API Key 和模型名称")

    # Normalize URL: append /chat/completions if not present
    url = api_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi, reply with just 'OK'."}],
        "max_tokens": 8,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            body = resp.json()
            reply = ""
            choices = body.get("choices", [])
            if choices:
                reply = choices[0].get("message", {}).get("content", "")
            return {"ok": True, "model": model, "reply": reply[:100]}
        else:
            detail = resp.text[:200]
            raise HTTPException(status_code=502, detail=f"API 返回 {resp.status_code}: {detail}")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"无法连接到 {api_url}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=502, detail="请求超时（15s）")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:200])


# -- Auth extras --


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, _admin=Depends(require_admin)):
    """Change password for the current user."""
    from .auth import _current_username
    username = _current_username.get()
    if not username:
        raise HTTPException(status_code=401, detail="未认证")

    if not req.new_password or len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="新密码至少4个字符")

    ok = _db.change_password(username, req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="旧密码不正确")
    return {"ok": True}


@router.get("/auth/profile")
def get_profile():
    """Get current user profile."""
    from .auth import _current_username
    username = _current_username.get()
    if not username:
        raise HTTPException(status_code=401, detail="未认证")

    user = _db.get_user_by_username(username)
    if not user:
        # Fallback to config credentials
        from .auth import _credentials
        user = {"username": _credentials.get("username", username), "created_at": None}
    return {
        "username": user["username"],
        "role": user.get("role", "admin"),
        "created_at": user.get("created_at"),
    }


class UpdateProfileRequest(BaseModel):
    username: str


@router.put("/auth/profile")
def update_profile(req: UpdateProfileRequest, _admin=Depends(require_admin)):
    """Update current user's username."""
    from .auth import _current_username
    username = _current_username.get()
    if not username:
        raise HTTPException(status_code=401, detail="未认证")

    new_name = req.username.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    ok = _db.update_username(username, new_name)
    if not ok:
        raise HTTPException(status_code=400, detail="更新用户名失败")
    return {"ok": True, "username": new_name}
