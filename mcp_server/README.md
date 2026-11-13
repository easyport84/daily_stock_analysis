# Daily Stock Analysis MCP adapter

This service exposes the existing DSA FastAPI endpoints as MCP tools; it does not duplicate the analysis engine.

## Tools

- `analyze_stock`: submit one stock for asynchronous analysis.
- `analyze_stocks`: submit multiple stocks for asynchronous analysis.
- `market_review`: submit a market review.
- `get_analysis_status`: retrieve task progress or the final result.

## Authentication and configuration

The MCP service rejects requests without a valid `Authorization: Bearer <MCP_BEARER_TOKEN>` header. Its upstream DSA API calls use a separate `DSA_API_BEARER_TOKEN`. Keep both values in Render's environment settings; never commit them or paste them into chat.

| Variable | Required | Purpose |
|---|---:|---|
| `DSA_API_BASE_URL` | yes | DSA API URL. Render Blueprint sets this to the private `dsa-api` host and port. |
| `DSA_API_BEARER_TOKEN` | yes | Shared only between the DSA API and this adapter. |
| `MCP_BEARER_TOKEN` | yes | Client credential for inbound MCP requests. |
| `DSA_API_TIMEOUT_SECONDS` | no | Upstream timeout in seconds; defaults to `45`. |
| `MCP_HOST` | no | Bind address; defaults to `0.0.0.0`. |
| `PORT` | no | Render's assigned listening port. Takes precedence over `MCP_PORT`. |
| `MCP_PORT` | no | Local listening port; defaults to `8001`. |
| `MCP_ALLOWED_HOSTS` | no | Extra comma-separated Host names when running behind another proxy. Render's service hostname is allowed automatically. |

The public `/healthz` endpoint only reports process liveness. All MCP routes, including `/mcp`, require bearer authentication. The upstream DSA API is a Render private service and also checks the separate bearer token.

## Deploy both services on Render

The repository-root `render.yaml` defines two services on the `feat/chatgpt-mcp-adapter` branch:

- `dsa-api`: private Docker service using `docker/Dockerfile`, run in API-only mode.
- `dsa-mcp`: public Docker web service using `mcp_server/Dockerfile`.

Create a Blueprint from this repository and branch in Render. Review the proposed plans before approving deployment. Render generates both service tokens and wires the DSA API address into the adapter. The DSA service gets a 10 GB persistent disk mounted at `/app/data`; the Docker image stores the SQLite database, logs, reports, and Longbridge cache beneath that mount.

For an actual CBRS analysis, set a supported DSA model-provider key directly in the `dsa-api` service's Render Environment page. Do not put provider credentials in this repository or in chat. DSA's `.env.example` documents supported providers. `TUSHARE_TOKEN` is for A-share data and is not required for US-listed CBRS. News search API keys are optional.

## Connect ChatGPT

The public MCP endpoint is:

```
https://<dsa-mcp-public-host>/mcp
```

In ChatGPT, add a custom MCP server, use the HTTPS endpoint, and configure the supported bearer/API-key authentication with the value of `MCP_BEARER_TOKEN` from Render's Environment page. Do not expose the DSA API publicly. ChatGPT's custom MCP connection flow may be limited by workspace plan and admin policy; check availability in the target ChatGPT workspace.

## Local run

Start the DSA API separately (normally on port 8000), then run the adapter:

```bash
cd mcp_server
python -m venv .venv
pip install -r requirements.txt
DSA_API_BASE_URL=http://127.0.0.1:8000 \
DSA_API_BEARER_TOKEN=<local-dsa-token> \
MCP_BEARER_TOKEN=<local-mcp-token> \
python server.py
```

The MCP Streamable HTTP endpoint is `http://localhost:8001/mcp`. Use temporary local tokens; do not use production credentials in local development.

## Upstream API contract

- `POST /api/v1/analysis/analyze` with `stock_code` or `stock_codes` and `async_mode=true`
- `POST /api/v1/analysis/market-review`
- `GET /api/v1/analysis/status/{task_id}`

Analysis output defaults to English; set `report_language` to `zh` or `ko` as needed. Model and market-data credentials remain configured on the DSA API host.
