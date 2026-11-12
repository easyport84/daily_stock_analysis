"""MCP adapter for the existing Daily Stock Analysis FastAPI service."""
from __future__ import annotations
import json, os
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.getenv("DSA_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_TOKEN = os.getenv("DSA_API_BEARER_TOKEN", "")
REQUEST_TIMEOUT = float(os.getenv("DSA_API_TIMEOUT_SECONDS", "45"))
mcp = FastMCP("Daily Stock Analysis", instructions="Use the existing DSA engine. Return its task IDs and reports without inventing missing data.", host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", "8001")))

async def request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if API_TOKEN: headers["Authorization"] = f"Bearer {API_TOKEN}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        response = await client.request(method, f"{API_BASE_URL}{path}", json=payload)
        response.raise_for_status()
        try: data = response.json()
        except ValueError: return {"status_code": response.status_code, "text": response.text}
        if isinstance(data, dict):
            data.setdefault("_http_status", response.status_code)
            return data
        return {"data": data, "_http_status": response.status_code}

def optional_fields(skills, report_language, analysis_phase):
    result = {}
    if skills: result["skills"] = skills
    if report_language: result["report_language"] = report_language
    if analysis_phase: result["analysis_phase"] = analysis_phase
    return result

@mcp.tool()
async def analyze_stock(stock_code: str, skills: list[str] | None = None, report_language: str = "en", analysis_phase: str = "auto") -> str:
    """Submit one stock code for asynchronous DSA analysis."""
    if not stock_code.strip(): raise ValueError("stock_code must not be empty")
    payload = {"stock_code": stock_code.strip(), "async_mode": True}
    payload.update(optional_fields(skills, report_language, analysis_phase))
    return json.dumps(await request("POST", "/api/v1/analysis/analyze", payload), ensure_ascii=False)

@mcp.tool()
async def analyze_stocks(stock_codes: list[str], skills: list[str] | None = None, report_language: str = "en", analysis_phase: str = "auto") -> str:
    """Submit multiple stock codes for asynchronous DSA analysis."""
    codes = [x.strip() for x in stock_codes if x and x.strip()]
    if not codes: raise ValueError("stock_codes must contain at least one code")
    payload = {"stock_codes": codes, "async_mode": True}
    payload.update(optional_fields(skills, report_language, analysis_phase))
    return json.dumps(await request("POST", "/api/v1/analysis/analyze", payload), ensure_ascii=False)

@mcp.tool()
async def market_review(region: str = "us", report_language: str = "en", send_notification: bool = False) -> str:
    """Start a market review for cn, hk, us, jp, kr, both, or a comma-separated subset."""
    payload = {"region": region, "report_language": report_language, "send_notification": send_notification}
    return json.dumps(await request("POST", "/api/v1/analysis/market-review", payload), ensure_ascii=False)

@mcp.tool()
async def get_analysis_status(task_id: str) -> str:
    """Get progress or final result for a DSA task ID."""
    if not task_id.strip(): raise ValueError("task_id must not be empty")
    return json.dumps(await request("GET", f"/api/v1/analysis/status/{task_id.strip()}"), ensure_ascii=False)

if __name__ == "__main__": mcp.run(transport="streamable-http")
