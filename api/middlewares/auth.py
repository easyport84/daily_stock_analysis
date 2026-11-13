# -*- coding: utf-8 -*-
"""
Auth middleware: protect /api/v1/* with either a server-to-server bearer token
or the existing admin session cookie.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth import COOKIE_NAME, is_auth_enabled, verify_session

logger = logging.getLogger(__name__)

API_BEARER_TOKEN_ENV = "DSA_API_BEARER_TOKEN"

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/health",
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _path_exempt(path: str) -> bool:
    """Check if path is exempt from auth."""
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


def _valid_bearer_token(request: Request, expected_token: str) -> bool:
    """Compare an Authorization bearer token without leaking timing information."""
    authorization = request.headers.get("authorization", "")
    scheme, separator, presented_token = authorization.partition(" ")
    return (
        bool(separator)
        and scheme.lower() == "bearer"
        and bool(presented_token.strip())
        and hmac.compare_digest(presented_token.strip(), expected_token)
    )


def _valid_admin_session(request: Request) -> bool:
    cookie_val = request.cookies.get(COOKIE_NAME)
    return bool(cookie_val and verify_session(cookie_val))


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect API routes with bearer auth and optional admin session auth."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        if _path_exempt(path) or not path.startswith("/api/v1/"):
            return await call_next(request)

        # A configured service token is enforced even when admin cookie auth is
        # disabled. This lets MCP authenticate to DSA without browser cookies.
        bearer_token = os.getenv(API_BEARER_TOKEN_ENV, "").strip()
        if bearer_token:
            if _valid_bearer_token(request, bearer_token):
                return await call_next(request)
            if is_auth_enabled() and _valid_admin_session(request):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "error": "unauthorized",
                    "message": "A valid bearer token or admin session is required",
                },
            )

        # Preserve the existing local deployment behavior when no service token
        # is configured.
        if not is_auth_enabled() or _valid_admin_session(request):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "message": "Login required",
            },
        )


def add_auth_middleware(app):
    """Add auth middleware to protect API routes.

    Bearer enforcement is controlled by DSA_API_BEARER_TOKEN. Admin cookie
    enforcement continues to follow ADMIN_AUTH_ENABLED.
    """
    app.add_middleware(AuthMiddleware)
