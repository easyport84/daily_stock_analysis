"""Auth0-protected MCP adapter for the existing Daily Stock Analysis API."""
from __future__ import annotations

import contextlib
import json
import os
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import uvicorn
from auth0_api_python import ApiClient, ApiClientOptions
from auth0_api_python.errors import VerifyAccessTokenError
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import Tool as MCPTool
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

API_BASE_URL = os.getenv("DSA_API_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
if "://" not in API_BASE_URL:
    # Render's fromService hostport value is host:port; private service traffic
    # uses HTTP within Render's private network.
    API_BASE_URL = f"http://{API_BASE_URL}"
parsed_api_url = urlsplit(API_BASE_URL)
if parsed_api_url.scheme not in {"http", "https"} or not parsed_api_url.netloc:
    raise RuntimeError("DSA_API_BASE_URL must be an http(s) URL or host:port")

API_TOKEN = os.getenv("DSA_API_BEARER_TOKEN", "").strip()
if not API_TOKEN:
    raise RuntimeError("DSA_API_BEARER_TOKEN must be set")

REQUEST_TIMEOUT = float(os.getenv("DSA_API_TIMEOUT_SECONDS", "45"))
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
# Render provides PORT=10000. Local Docker runs use MCP_PORT=8001.
MCP_PORT = int(os.getenv("PORT") or os.getenv("MCP_PORT", "8001"))

AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "").strip()
AUTH0_DOMAIN = AUTH0_DOMAIN.removeprefix("https://").removeprefix("http://").rstrip("/")
if AUTH0_DOMAIN and ("/" in AUTH0_DOMAIN or ":" in AUTH0_DOMAIN):
    raise RuntimeError("AUTH0_DOMAIN must be a tenant hostname without a path or port")

MCP_PUBLIC_URL = os.getenv("MCP_PUBLIC_URL", "").strip().rstrip("/")
if not MCP_PUBLIC_URL:
    render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
    MCP_PUBLIC_URL = (
        f"https://{render_hostname}"
        if render_hostname
        else f"http://localhost:{MCP_PORT}"
    )
parsed_public_url = urlsplit(MCP_PUBLIC_URL)
if parsed_public_url.scheme not in {"http", "https"} or not parsed_public_url.netloc:
    raise RuntimeError("MCP_PUBLIC_URL must be an http(s) URL")
# Auth0's API identifier must match the resource value advertised below.
# On Render this defaults to the service's stable public HTTPS origin.
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", MCP_PUBLIC_URL).strip().rstrip("/")
parsed_audience = urlsplit(AUTH0_AUDIENCE)
if parsed_audience.scheme not in {"http", "https"} or not parsed_audience.netloc:
    raise RuntimeError("AUTH0_AUDIENCE must be an http(s) URL")

AUTH0_ISSUER = f"https://{AUTH0_DOMAIN}" if AUTH0_DOMAIN else ""
RESOURCE_METADATA_URL = (
    f"{MCP_PUBLIC_URL}/.well-known/oauth-protected-resource"
)
AUTH0_CLIENT = (
    ApiClient(ApiClientOptions(domain=AUTH0_DOMAIN, audience=AUTH0_AUDIENCE))
    if AUTH0_DOMAIN
    else None
)

TOOL_SCOPES = {
    "analyze_stock": ["dsa:analysis:run"],
    "analyze_stocks": ["dsa:analysis:run"],
    "market_review": ["dsa:analysis:run"],
    "get_analysis_status": ["dsa:analysis:status"],
}
ALL_SCOPES = sorted({scope for scopes in TOOL_SCOPES.values() for scope in scopes})


class OAuthFastMCP(FastMCP):
    """Add ChatGPT's per-tool OAuth declarations to the SDK-generated tool list."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        for tool in tools:
            tool.meta = {
                "securitySchemes": [
                    {"type": "oauth2", "scopes": TOOL_SCOPES.get(tool.name, [])}
                ]
            }
        return tools


mcp = OAuthFastMCP(
    "Daily Stock Analysis",
    instructions="Use the existing DSA engine. Return its task IDs and reports without inventing missing data.",
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=True,
)


async def request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_TOKEN}",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        response = await client.request(
            method,
            f"{API_BASE_URL}{path}",
            json=payload,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"status_code": response.status_code, "text": response.text}
        if isinstance(data, dict):
            data.setdefault("_http_status", response.status_code)
            return data
        return {"data": data, "_http_status": response.status_code}


def optional_fields(
    skills: list[str] | None,
    report_language: str | None,
    analysis_phase: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if skills:
        result["skills"] = skills
    if report_language:
        result["report_language"] = report_language
    if analysis_phase:
        result["analysis_phase"] = analysis_phase
    return result


def require_scope(ctx: Context, required_scope: str) -> None:
    """Enforce the OAuth scope declared for this MCP tool."""
    try:
        claims = ctx.request_context.request.state.auth
    except (AttributeError, LookupError, ValueError):
        claims = {}
    granted_scopes = set(str(claims.get("scope", "")).split())
    granted_scopes.update(claims.get("permissions", []))
    if required_scope not in granted_scopes:
        raise PermissionError(
            f"Missing OAuth permission {required_scope}; reconnect the ChatGPT app "
            "and approve the requested permission."
        )


@mcp.tool()
async def analyze_stock(
    stock_code: str,
    skills: list[str] | None = None,
    report_language: str = "en",
    analysis_phase: str = "auto",
    ctx: Context | None = None,
) -> str:
    """Submit one stock code for asynchronous DSA analysis."""
    if ctx is None:
        raise PermissionError("An authenticated MCP request is required.")
    require_scope(ctx, "dsa:analysis:run")
    if not stock_code.strip():
        raise ValueError("stock_code must not be empty")
    payload = {"stock_code": stock_code.strip(), "async_mode": True}
    payload.update(optional_fields(skills, report_language, analysis_phase))
    return json.dumps(
        await request("POST", "/api/v1/analysis/analyze", payload),
        ensure_ascii=False,
    )


@mcp.tool()
async def analyze_stocks(
    stock_codes: list[str],
    skills: list[str] | None = None,
    report_language: str = "en",
    analysis_phase: str = "auto",
    ctx: Context | None = None,
) -> str:
    """Submit multiple stock codes for asynchronous DSA analysis."""
    if ctx is None:
        raise PermissionError("An authenticated MCP request is required.")
    require_scope(ctx, "dsa:analysis:run")
    codes = [code.strip() for code in stock_codes if code and code.strip()]
    if not codes:
        raise ValueError("stock_codes must contain at least one code")
    payload = {"stock_codes": codes, "async_mode": True}
    payload.update(optional_fields(skills, report_language, analysis_phase))
    return json.dumps(
        await request("POST", "/api/v1/analysis/analyze", payload),
        ensure_ascii=False,
    )


@mcp.tool()
async def market_review(
    region: str = "us",
    report_language: str = "en",
    send_notification: bool = False,
    ctx: Context | None = None,
) -> str:
    """Start a market review for cn, hk, us, jp, kr, both, or a comma-separated subset."""
    if ctx is None:
        raise PermissionError("An authenticated MCP request is required.")
    require_scope(ctx, "dsa:analysis:run")
    payload = {
        "region": region,
        "report_language": report_language,
        "send_notification": send_notification,
    }
    return json.dumps(
        await request("POST", "/api/v1/analysis/market-review", payload),
        ensure_ascii=False,
    )


@mcp.tool()
async def get_analysis_status(task_id: str, ctx: Context | None = None) -> str:
    """Get progress or final result for a DSA task ID."""
    if ctx is None:
        raise PermissionError("An authenticated MCP request is required.")
    require_scope(ctx, "dsa:analysis:status")
    if not task_id.strip():
        raise ValueError("task_id must not be empty")
    encoded_task_id = quote(task_id.strip(), safe="")
    return json.dumps(
        await request("GET", f"/api/v1/analysis/status/{encoded_task_id}"),
        ensure_ascii=False,
    )


def _normalize_host(value: str) -> str:
    try:
        return (urlsplit(f"//{value.strip()}").hostname or "").lower()
    except ValueError:
        return ""


def _allowed_hosts() -> set[str]:
    values = [
        os.getenv("RENDER_EXTERNAL_HOSTNAME", ""),
        *os.getenv("MCP_ALLOWED_HOSTS", "").split(","),
    ]
    hosts = {_normalize_host(value) for value in values if value.strip()}
    hosts.discard("")
    if not hosts:
        hosts.update({"localhost", "127.0.0.1", "::1"})
    return hosts


async def _respond(
    scope: Scope,
    receive: Receive,
    send: Send,
    status_code: int,
    body: bytes,
    content_type: bytes = b"application/json",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class OAuthAuthAndHostGuard:
    """Validate Auth0 access tokens and reject requests for unconfigured hostnames."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if path == "/healthz" and method == "GET":
            await _respond(scope, receive, send, 200, b'{"status":"ok"}')
            return

        request_headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        host = _normalize_host(request_headers.get(b"host", b"").decode("latin-1"))
        if host not in _allowed_hosts():
            await _respond(
                scope,
                receive,
                send,
                421,
                b"Invalid Host header",
                content_type=b"text/plain; charset=utf-8",
            )
            return

        if path == "/.well-known/oauth-protected-resource" and method in {"GET", "OPTIONS"}:
            if not AUTH0_DOMAIN:
                await _respond(
                    scope,
                    receive,
                    send,
                    503,
                    b'{"error":"oauth_not_configured"}',
                )
                return
            await self.app(scope, receive, send)
            return

        if AUTH0_CLIENT is None:
            await _respond(
                scope,
                receive,
                send,
                503,
                b'{"error":"oauth_not_configured"}',
            )
            return

        authorization = request_headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, presented_token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not presented_token.strip():
            await self._unauthorized(scope, receive, send)
            return

        try:
            claims = await AUTH0_CLIENT.verify_access_token(
                presented_token.strip(),
                required_claims=["sub"],
            )
        except VerifyAccessTokenError:
            await self._unauthorized(scope, receive, send)
            return

        # FastMCP exposes the Starlette request in its tool Context.
        scope.setdefault("state", {})["auth"] = claims
        await self.app(scope, receive, send)

    async def _unauthorized(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        challenge = (
            f'Bearer resource_metadata="{RESOURCE_METADATA_URL}", '
            'error="invalid_token", error_description="Authentication is required."'
        )
        await _respond(
            scope,
            receive,
            send,
            401,
            b'{"error":"unauthorized"}',
            extra_headers=[(b"www-authenticate", challenge.encode("latin-1"))],
        )


metadata_routes = (
    create_protected_resource_routes(
        resource_url=AUTH0_AUDIENCE,
        authorization_servers=[AUTH0_ISSUER],
        scopes_supported=ALL_SCOPES,
        resource_name="Daily Stock Analysis",
    )
    if AUTH0_DOMAIN
    else []
)
mcp_http_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


starlette_app = Starlette(
    routes=[
        *metadata_routes,
        Mount("/", app=mcp_http_app),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    app = OAuthAuthAndHostGuard(starlette_app)
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
