# -*- coding: utf-8 -*-
"""Token-based authentication middleware."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# Paths that don't require authentication
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/check"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate Bearer token on all /api/ paths except login."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip non-API paths (static files, SPA)
        if not path.startswith("/api/"):
            return await call_next(request)

        # Skip public paths
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # Import here to avoid circular import; module is initialized by lifespan
        from routes.auth import verify_token

        # Extract token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"detail": "未认证"}, status_code=401)

        token = auth_header[7:]
        if not verify_token(token):
            return JSONResponse({"detail": "认证已过期，请重新登录"}, status_code=401)

        return await call_next(request)
