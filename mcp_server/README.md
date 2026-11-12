# Daily Stock Analysis MCP adapter

This service exposes the existing DSA FastAPI endpoints as MCP tools; it does not duplicate the analysis engine.

## Tools
- `analyze_stock`: submit one stock for asynchronous analysis.
- `analyze_stocks`: submit multiple stocks for asynchronous analysis.
- `market_review`: submit a market review.
- `get_analysis_status`: retrieve task progress or the final result.

## Configuration
| Variable | Default | Purpose |
|---|---|---|
| `DSA_API_BASE_URL` | `http://127.0.0.1:8000` | DSA API base URL |
| `DSA_API_BEARER_TOKEN` | empty | Optional bearer token for a compatible protected API gateway |
| `DSA_API_TIMEOUT_SECONDS` | `45` | Upstream timeout |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8001` | MCP port |

Never commit API keys or credentials. Keep both services behind HTTPS and authentication; do not expose an unauthenticated stock-analysis endpoint publicly.

## Local run
Start DSA API separately (normally port 8000), then run:
```bash
cd mcp_server
python -m venv .venv
pip install -r requirements.txt
DSA_API_BASE_URL=http://127.0.0.1:8000 python server.py
```
The MCP Streamable HTTP endpoint is `http://localhost:8001/mcp`.

## Container
```bash
docker build -t daily-stock-analysis-mcp .
docker run --rm -p 8001:8001 -e DSA_API_BASE_URL=https://YOUR-DSA-API-HOST daily-stock-analysis-mcp
```

For ChatGPT remote use, deploy the container to a host with a stable public HTTPS URL, protect it with authentication, and register `https://YOUR-HOST/mcp` as a remote MCP server in a ChatGPT app/connector configuration. GitHub source code alone does not deploy or register an app.

## Upstream API contract
- `POST /api/v1/analysis/analyze` with `stock_code` or `stock_codes` and `async_mode=true`
- `POST /api/v1/analysis/market-review`
- `GET /api/v1/analysis/status/{task_id}`

Analysis output defaults to English; set `report_language` to `zh` or `ko` as needed. Model and market-data credentials remain configured on the DSA API host.