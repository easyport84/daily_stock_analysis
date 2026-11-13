# Daily Stock Analysis MCP adapter

This service exposes the existing DSA API as MCP tools; it does not duplicate the analysis engine.

## Tools

- `analyze_stock`: submit one stock for asynchronous analysis.
- `analyze_stocks`: submit multiple stocks for asynchronous analysis.
- `market_review`: submit a market review.
- `get_analysis_status`: retrieve task progress or the final result.

## Authentication and configuration

The public MCP endpoint uses Auth0 OAuth. It validates the Auth0 access token on every MCP request, checks the token audience, issuer, expiry, and subject, and checks the required OAuth scope before running each tool. The `/healthz` endpoint and OAuth protected-resource metadata are public; the MCP endpoint is not. If Auth0 has not been configured, MCP requests fail closed with HTTP 503.

The adapter uses a separate DSA bearer token for calls to the private DSA API. Keep that value in Render's environment settings; never commit it or paste it into chat.

| Variable | Required | Purpose |
|---|---:|---|
| `DSA_API_BASE_URL` | yes | DSA API URL. Render Blueprint sets this to the private `dsa-api` host and port. |
| `DSA_API_BEARER_TOKEN` | yes | Shared only between the DSA API and this adapter. |
| `AUTH0_DOMAIN` | yes for MCP access | Auth0 tenant hostname, such as `my-tenant.us.auth0.com`; no client secret is used by the server. |
| `AUTH0_AUDIENCE` | no | Auth0 API identifier. Defaults to `MCP_PUBLIC_URL`, which defaults to Render's public HTTPS hostname. It must exactly match the Auth0 API identifier. |
| `MCP_PUBLIC_URL` | no | Canonical public resource URL; on Render it is derived from `RENDER_EXTERNAL_HOSTNAME`. Set this only for local development or a custom domain. |
| `DSA_API_TIMEOUT_SECONDS` | no | Upstream timeout in seconds; defaults to `45`. |
| `MCP_HOST` | no | Bind address; defaults to `0.0.0.0`. |
| `PORT` | no | Render's assigned listening port. Takes precedence over `MCP_PORT`. |
| `MCP_PORT` | no | Local listening port; defaults to `8001`. |
| `MCP_ALLOWED_HOSTS` | no | Extra comma-separated Host names when running behind another proxy. Render's service hostname is allowed automatically. |

The MCP tools request these Auth0 API scopes:

- `dsa:analysis:run` for `analyze_stock`, `analyze_stocks`, and `market_review`.
- `dsa:analysis:status` for `get_analysis_status`.

## Deploy both services on Render

The repository-root `render.yaml` defines two Singapore services on the `feat/chatgpt-mcp-adapter` branch:

- `dsa-api`: private Docker service using `docker/Dockerfile`, run in API-only mode.
- `dsa-mcp`: public Docker web service using `mcp_server/Dockerfile`, protected by Auth0 OAuth.

Create a Blueprint from this repository and branch in Render. The Blueprint generates the internal DSA API bearer token and shares it only with the private API and MCP services. The DSA service gets a 10 GB persistent disk mounted at `/app/data`; the Docker image keeps the SQLite database, logs, reports, and Longbridge cache under that mount.

The API service is configured as `1c-2g` and the disk is 10 GB. The MCP service uses Render's Free plan, which may sleep while idle. Review the Blueprint's current price before approving it. For an actual CBRS analysis, set one supported DSA model-provider key directly in the `dsa-api` service's Render Environment page. Never put provider credentials in this repository or in chat. DSA's `.env.example` documents supported providers. `TUSHARE_TOKEN` is for A-share data and is not required for US-listed CBRS. News search keys are optional.

## Connect Auth0 and ChatGPT

1. Create or sign in to an Auth0 tenant. Use its default tenant domain; a custom domain is unnecessary.
2. In Render, open the `dsa-mcp` service and copy its public hostname. In Auth0, create an API with:
   - Identifier: `https://<your-dsa-mcp-hostname>` (no trailing slash and no `/mcp` path)
   - Signing algorithm: `RS256`
   - Scopes: `dsa:analysis:run` and `dsa:analysis:status`
   - Enable **Allow Offline Access** in the API settings so ChatGPT can renew access after the first token expires.
3. In Auth0 tenant Settings > Advanced, enable **Resource Parameter Compatibility Profile**, **Include Issuer in Authorization Responses**, and **Client ID Metadata Document Registration**. Enable **Dynamic CIMD Registration** if the setting is available to your tenant.
4. Register or approve ChatGPT as an Auth0 third-party application. If Dynamic CIMD is enabled, approve the client when prompted. Otherwise, use Auth0 Applications > Applications > Create Application > Import from URL and enter ChatGPT's current client metadata URL from [OpenAI's auth guide](https://developers.openai.com/plugins/build/auth). Grant that application access to this API with only the two DSA scopes above.
5. Promote the Auth0 login connection you want to use to a domain-level connection, as Auth0 requires for third-party clients. Limit sign-in to the people who should use these tools. For a personal service, disable public sign-up and create your own Auth0 user.
6. In Render's `dsa-mcp` Environment settings, set `AUTH0_DOMAIN` to the tenant hostname. Leave `AUTH0_AUDIENCE` unset so it uses the Render hostname as the audience. A redeploy applies the setting.
7. In ChatGPT web, add a custom MCP server using `https://<your-dsa-mcp-hostname>/mcp` and choose OAuth when prompted. ChatGPT uses OAuth authorization code with PKCE; this service does not use an Auth0 client secret.

Auth0's free plan currently includes up to 25,000 monthly active users. [Auth0's MCP authorization guide](https://auth0.com/ai/docs/mcp/get-started/authorization-for-your-mcp-server) documents tenant settings and API scopes. [Auth0's CIMD guide](https://auth0.com/docs/get-started/auth0-overview/create-applications/register-applications-with-cimd) documents client registration. [OpenAI's ChatGPT setup guide](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) currently supports custom MCP connections on ChatGPT web for Business and Enterprise/Edu workspaces; your workspace permissions must allow adding them. OpenAI recommends refresh tokens for persistent OAuth connections; Auth0's **Allow Offline Access** setting enables that for the API.

## Local run

Start the DSA API separately (normally on port 8000), create an Auth0 API with identifier `http://localhost:8001` and the two scopes above, then run:

```bash
cd mcp_server
python -m venv .venv
pip install -r requirements.txt
DSA_API_BASE_URL=http://127.0.0.1:8000 \
DSA_API_BEARER_TOKEN=<local-dsa-token> \
AUTH0_DOMAIN=<your-tenant-domain> \
AUTH0_AUDIENCE=http://localhost:8001 \
MCP_PUBLIC_URL=http://localhost:8001 \
python server.py
```

The MCP Streamable HTTP endpoint is `http://localhost:8001/mcp`. Use temporary local credentials; do not use production credentials in local development.

## Upstream API contract

- `POST /api/v1/analysis/analyze` with `stock_code` or `stock_codes` and `async_mode=true`
- `POST /api/v1/analysis/market-review`
- `GET /api/v1/analysis/status/{task_id}`

Analysis output defaults to English; set `report_language` to `zh` or `ko` as needed. Model and market-data credentials remain configured on the DSA API host.
