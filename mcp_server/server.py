"""Authenticated MCP adapter for the existing Daily Stock Analysis API."""
from __future__ import annotations

import hmac
import json
import os
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
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
MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN", "").strip()
REQUEST_TIMEOUT = float(os.getenv("DSA_API_TIMEOUT_SECONDS", "45"))
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
# Render provides PORT=10000. Local Docker runs use MCP_PORT=8001.
MCP_PORT = int(os.getenv("PORT") or os.getenv("MCP_PORT", "8001"))

if not API_TOKEN:
    raise RuntimeError("DSA_API_BEARER_TOKEN must be set")
if not MCP_BEARER_TOKEN:
    raise RuntimeError("MCP_BEARER_TOKEN must be set")

mcp = FastMCP(
    "Daily Stock Analysis",
    instructions="Use the existing DSA engine. Return its task IDs and reports without inventing missing data.",
    host=MCP_HOST,
    port=MCP_PORT,
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


@mcp.tool()
async def analyze_stock(
    stock_code: str,
    skills: list[str] | None = None,
    report_language: str = "en",
    analysis_phase: str = "auto",
) -> str:
    """Submit one stock code for asynchronous DSA analysis."""
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
) -> str:
    """Submit multiple stock codes for asynchronous DSA analysis."""
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
) -> str:
    """Start a market review for cn, hk, us, jp, kr, both, or a comma-separated subset."""
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
async def get_analysis_status(task_id: str) -> str:
    """Get progress or final result for a DSA task ID."""
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


class BearerAuthAndHostGuard:
    """Protect the MCP endpoint and reject requests for unconfigured hostnames."""

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

        authorization = request_headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, presented_token = authorization.partition(" ")
        valid_token = (
            bool(separator)
            and scheme.lower() == "bearer"
            and bool(presented_token.strip())
            and hmac.compare_digest(presented_token.strip(), MCP_BEARER_TOKEN)
        )
        if not valid_token:
            await _respond(
                scope,
                receive,
                send,
                401,
                b'{"error":"unauthorized"}',
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return

        await self.app(scope, receive, send)


if __name__ == "__main__":
    app = BearerAuthAndHostGuard(mcp.streamable_http_app())
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
