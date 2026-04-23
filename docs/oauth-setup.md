# First-time OAuth setup

Clio uses OAuth 2.0 authorization-code flow. You do this dance once to get a refresh token, then the server handles access-token refreshes automatically forever (or until the refresh token is revoked, which is rare).

## 1. Create a Clio developer application

Sign in to Clio and go to **Settings → Developer Applications**. Direct URL by region:

- US: https://app.clio.com/settings/developer_applications
- CA: https://ca.app.clio.com/settings/developer_applications
- EU/UK: https://eu.app.clio.com/settings/developer_applications
- AU: https://au.app.clio.com/settings/developer_applications

Click **New Application**. Fill in:

- **Name**: anything (e.g. `Clio MCP`)
- **Redirect URI**: `http://localhost:8765/callback`
  (Clio just needs the code to land somewhere — the page will fail to load when redirected, that's expected)
- **Scope**: check every scope you might want — adding scopes later requires re-running this dance. For this MCP server's tools, you need at least: `contacts`, `matters`, `activities`, `users`. Bills/calendar/documents if you plan to extend.

Save. You get back:

- **Client ID** (visible)
- **Client Secret** (shown once — copy it now)

## 2. Get an authorization code

Visit this URL in your browser (substituting `YOUR_CLIENT_ID` and your region's hostname):

```
https://app.clio.com/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8765/callback
```

Approve the request. The browser will redirect to `http://localhost:8765/callback?code=XXXXXXXX...` and show a "connection refused" error. **That's fine** — copy the value of `code=` out of the URL bar.

The code is single-use and expires in ~10 minutes. Move quickly.

## 3. Exchange the code for a refresh token

```bash
curl -X POST https://app.clio.com/oauth/token \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "grant_type=authorization_code" \
  -d "code=THE_CODE_FROM_STEP_2" \
  -d "redirect_uri=http://localhost:8765/callback"
```

Response:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 2592000,
  "refresh_token": "..."
}
```

**Copy the `refresh_token`** — that's what goes into your `.env`. The `access_token` you can discard; the server will mint fresh ones as needed.

## 4. Configure the server

```bash
cp .env.example .env
# Edit .env, paste in CLIO_CLIENT_ID, CLIO_CLIENT_SECRET, CLIO_REFRESH_TOKEN
chmod 600 .env
```

## 5. Verify

```bash
python clio_mcp_server.py --stdio
# Or via MCP Inspector:
mcp dev clio_mcp_server.py
```

In the inspector, call `clio_who_am_i`. Expect:

```json
{
  "status_code": 200,
  "body": {
    "data": {
      "id": <your user id>,
      "name": "<your name>",
      ...
    }
  }
}
```

If you get a 401, the refresh token is bad — re-run the dance.

## Re-authorizing later

If your refresh token gets revoked (you removed the app from Clio, scopes changed, etc.):

1. Repeat steps 2-3 to get a new code → refresh token.
2. Update `CLIO_REFRESH_TOKEN` in `.env`.
3. Delete `.clio_tokens.json` if it exists (cached state from rotated tokens).
4. Restart the MCP server.
