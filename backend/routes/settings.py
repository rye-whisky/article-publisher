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


@router.get("/settings/llm-tasks")
def get_llm_tasks():
    """List available LLM tasks and their current config (keys masked)."""
    from services.llm_service import LLMService
    tasks = LLMService.list_tasks()
    result = {}
    svc = LLMService(_db)
    for task_id, task_name in tasks.items():
        result[task_id] = {
            "name": task_name,
            "config": svc.get_task_settings(task_id),
        }
    return {"tasks": result, "factories": LLMService.list_factories()}


@router.put("/settings")
def update_settings(req: SettingsUpdate, _admin=Depends(require_admin)):
    """Batch update settings."""
    _db.set_settings_batch(req.settings)
    return {"ok": True}


@router.post("/settings/test-llm/{task}")
async def test_llm_task(task: str, _admin=Depends(require_admin)):
    """Test LLM connection for a specific task (abstract, edit, etc.)."""
    import asyncio
    from services.llm_service import LLMService

    svc = LLMService(_db)
    result = await asyncio.get_event_loop().run_in_executor(None, svc.test_connection, task)

    if result["ok"]:
        return {"ok": True, "task": task, "reply": result["reply"]}
    else:
        raise HTTPException(status_code=502, detail=result["error"] or "连接失败")


@router.post("/settings/test-llm")
async def test_llm(_admin=Depends(require_admin)):
    """Test LLM connection (legacy, defaults to abstract task)."""
    return await test_llm_task("abstract", _admin)


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
