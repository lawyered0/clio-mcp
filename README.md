# clio-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for [Clio Manage](https://www.clio.com/clio-manage/), the practice management software for law firms.

Lets [Claude](https://claude.ai) (or any MCP client) read and write your Clio data — contacts, matters, activities — directly from chat. Built and tested against Clio's v4 REST API.

**Includes a documented workaround** for Clio's silent rejection of `billing_method` on matter creation — see [docs/flat-fee-workaround.md](docs/flat-fee-workaround.md) and the [Confirmed Clio API quirks](#confirmed-clio-api-quirks) section below.

## Tools (10)

| Tool | Purpose |
|---|---|
| `clio_who_am_i` | Auth check — confirm credentials work |
| `clio_create_company_contact` | Create entity client (Inc., LLC, etc.) |
| `clio_create_person_contact` | Create individual client |
| `clio_create_matter` | Create matter, optional default attorney |
| `clio_create_flat_fee_activity` | Add a flat-fee billable line item — the workaround |
| `clio_find_contact` | Search by name and/or email |
| `clio_find_matter` | Search by display_number, query, or client_id |
| `clio_delete_matter` | Cleanup test data |
| `clio_delete_contact` | Cleanup test data |
| `clio_api_request` | Generic v4 API escape hatch |

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/Lawyered0/clio-mcp.git
cd clio-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Get OAuth credentials from Clio (one-time, ~5 min)
#    See "Getting Clio OAuth credentials" below for the full dance.
#    You'll end up with: CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN.

# 3. Configure
cp .env.example .env
# edit .env with your credentials and (optional) default attorney id

# 4. Test it works (Ctrl+C to exit)
python clio_mcp_server.py --stdio

# 5. Wire it into your MCP client (verified paths below)
#    - Claude Desktop:   build a DXT (see docs/claude-desktop-dxt.md)  ← recommended
#    - Claude Code CLI:  edit ~/.claude/settings.json (see below)
#    - MCP Inspector:    mcp dev clio_mcp_server.py  (for development)
```

## Verified clients

This server has been used in production against the following MCP clients:

| Client | Transport | How to wire | Status |
|---|---|---|---|
| **Claude Desktop** (Code tab and regular chat) | stdio | DXT extension — see [docs/claude-desktop-dxt.md](docs/claude-desktop-dxt.md) | ✅ Verified |
| **Claude Code CLI** | stdio | `~/.claude/settings.json` — see below | ✅ Verified |
| **MCP Inspector** | stdio | `mcp dev clio_mcp_server.py` | ✅ Verified |
| Cowork / claude.ai web / other URL-based hosts | HTTPS | Would need a public tunnel + auth on the server | ⚠️ Not pursued — see [HTTP mode notes](#http-mode-not-recommended-yet) |

**TL;DR:** if you're on Mac, install via DXT into Claude Desktop. If you're already a Claude Code CLI user, the JSON config is two lines. Either path takes ~5 minutes once you have OAuth credentials.

### HTTP mode (not recommended yet)

The server can also run as an HTTP service:

```bash
python clio_mcp_server.py    # binds 127.0.0.1:8765/mcp by default
```

This was built for use with URL-based connectors (Cowork, claude.ai web, etc.) but those hosts typically require HTTPS, often reject `127.0.0.1` URLs, and may have additional security policies. Making this safe for production use means: terminating TLS (e.g. Caddy with `tls internal`), exposing it via a tunnel (e.g. Cloudflare Tunnel), and adding header-based auth inside the server. **None of that is implemented or verified.** PRs welcome.

### Claude Code CLI config

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "clio": {
      "command": "/absolute/path/to/clio-mcp/.venv/bin/python",
      "args": [
        "/absolute/path/to/clio-mcp/clio_mcp_server.py",
        "--stdio"
      ]
    }
  }
}
```

Restart your Claude Code session, then try `clio_who_am_i` to verify.

### Claude Desktop config (DXT)

Claude Desktop uses DXT extensions. See [docs/claude-desktop-dxt.md](docs/claude-desktop-dxt.md) for a working manifest template and install instructions. **Note:** Claude Desktop requires a full app restart (Cmd+Q + relaunch) before a newly-installed DXT appears in any session's tool registry.

### .env

```
CLIO_CLIENT_ID=<from Clio developer portal>
CLIO_CLIENT_SECRET=<from Clio developer portal>
CLIO_REFRESH_TOKEN=<from initial OAuth dance — see docs/oauth-setup.md>

# Optional: defaults to US (app.clio.com). For other regions:
#   CA: https://ca.app.clio.com/api/v4/  +  https://ca.app.clio.com/oauth/token
#   EU: https://eu.app.clio.com/api/v4/  +  https://eu.app.clio.com/oauth/token
#   AU: https://au.app.clio.com/api/v4/  +  https://au.app.clio.com/oauth/token
CLIO_BASE_URL=https://app.clio.com/api/v4/
CLIO_TOKEN_URL=https://app.clio.com/oauth/token

# Optional: if set, used as the default responsible_attorney + originating_attorney
# on clio_create_matter when the caller doesn't pass attorney_id explicitly.
CLIO_DEFAULT_ATTORNEY_ID=
```

`chmod 600 .env` for hygiene.

## Getting Clio OAuth credentials (one-time)

This is the trickiest part of setup. Clio uses OAuth 2.0 authorization-code flow — you do this dance once to mint a refresh token, then the server handles access-token refreshes automatically. The refresh token is long-lived, so you should only ever do this once per OAuth app (unless the token gets revoked).

### Step 1 — Create a developer application in Clio

Sign in to Clio and go to **Settings → Developer Applications**. Direct URL by region:

- US: `https://app.clio.com/settings/developer_applications`
- CA: `https://ca.app.clio.com/settings/developer_applications`
- EU/UK: `https://eu.app.clio.com/settings/developer_applications`
- AU: `https://au.app.clio.com/settings/developer_applications`

Click **New Application**. Fill in:

- **Name**: anything (e.g. `Clio MCP`)
- **Redirect URI**: `http://localhost:8765/callback`
  (Clio just needs the code to land somewhere — the page will fail to load when you're redirected there, that's expected and fine)
- **Scope**: check **every scope you might want to use** — adding scopes later requires re-running this whole dance. At minimum: `contacts`, `matters`, `activities`, `users`. Add `bills`, `calendar`, `documents` if you'll extend.

Save. You get back:

- **Client ID** (visible in the app list anytime)
- **Client Secret** (shown once on creation — copy it now, you cannot retrieve it later)

### Step 2 — Get an authorization code

Visit this URL in your browser, substituting `YOUR_CLIENT_ID` and your region's host:

```
https://app.clio.com/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8765/callback
```

Approve. Your browser will redirect to `http://localhost:8765/callback?code=XXXXXXXX...` and show a "connection refused" error page. **Ignore the error** — copy the `code=XXXXXXXX` value out of the browser's address bar.

The code is single-use and expires in ~10 minutes. Move quickly to Step 3.

### Step 3 — Exchange the code for a refresh token

```bash
curl -X POST https://app.clio.com/oauth/token \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "grant_type=authorization_code" \
  -d "code=THE_CODE_FROM_STEP_2" \
  -d "redirect_uri=http://localhost:8765/callback"
```

(Substitute your region's host if not US.)

Response:

```json
{
  "access_token": "<short-lived; ignore>",
  "token_type": "bearer",
  "expires_in": 2592000,
  "refresh_token": "<this is the one you want>"
}
```

**Copy the `refresh_token`** — that's what goes into `.env` as `CLIO_REFRESH_TOKEN`. The `access_token` you can discard; the server mints fresh ones on demand.

### Step 4 — Drop into `.env`

```
CLIO_CLIENT_ID=<from Step 1>
CLIO_CLIENT_SECRET=<from Step 1>
CLIO_REFRESH_TOKEN=<from Step 3>
```

Run `clio_who_am_i` via your MCP client. If it returns 200 with your user record — you're done forever. If 401 — re-run from Step 2 (the code expired, or the redirect URI didn't match exactly).

### Common gotchas

- **`redirect_uri` must match EXACTLY** between the app config, the `/oauth/authorize` URL, and the `/oauth/token` POST — including trailing slash, port, and protocol. Mismatches return generic `400 invalid_grant`.
- **Don't reuse the code** — it's single-use. If you get `invalid_grant` on Step 3, the code probably expired (10 min limit) or was already used.
- **Region matters** — if your Clio account is on `ca.app.clio.com`, use that host throughout. Mixing US and non-US endpoints in the dance returns `400 invalid_grant` or `401` later.
- **Scope changes require re-doing the dance.** If you add `bills` to the app's scopes later, you need a new auth code → new refresh token. Existing tokens don't auto-acquire new scopes.

For re-authorization (if your refresh token ever gets revoked) and additional troubleshooting, see [docs/oauth-setup.md](docs/oauth-setup.md).

## Confirmed Clio API quirks

These were discovered empirically against the live API. Trust them, don't re-derive:

### Region routing
Clio runs on regional hosts. **Mixing region endpoints in a single request/response cycle returns 401 invalid_token** — a token minted at one region won't authenticate against another region's API. Pick one host and stick with it for both OAuth and API calls.

| Region | Host |
|---|---|
| US | `app.clio.com` |
| CA | `ca.app.clio.com` |
| EU/UK | `eu.app.clio.com` |
| AU | `au.app.clio.com` |

### `billing_method` is silently ignored on POST /matters.json
This is the big one. **Every value sent for `billing_method` (`"flat"`, `"Flat"`, `"FLAT"`, `"FlatRate"`, `"flat_fee"`, `"contingency"`, integers, etc.) results in `billing_method: "hourly"` on subsequent GET.** PATCH after creation also returns 200 but doesn't change the value. ~16 companion field guesses (`flat_rate_amount`, `flat_fee_amount`, `rate`, `matter_rate`, `billing_preference`, etc.) all silently ignored too. The Clio web UI uses a private endpoint not exposed in the public REST API.

**Workaround**: leave matters as `"hourly"` and add a flat-amount Activity. See [docs/flat-fee-workaround.md](docs/flat-fee-workaround.md) and use `clio_create_flat_fee_activity`.

### TimeEntry total math
`TimeEntry.total = quantity_in_hours × rate`, **not** `× price`. So a TimeEntry with `quantity_in_hours: 0` always totals $0 regardless of `price`. For flat-fee line items, use `ExpenseEntry` (`total = quantity × price`) — `qty=1, price=N → total=N`.

### Activity field-name aliases
- POST accepts both `description` and `note` for the line-item text. **GET only accepts `note` in `?fields=...`** — querying `description` returns 400 InvalidFields.
- `rate` is NOT a valid GET field on activities (returns 400). Use `price` × `quantity`.

### `matter_id` filter footgun on /activities.json
List filter param is `matter_id` (singular int). **`matter` or `matter[id]` are silently ignored and return account-wide activities** — typo here returns wrong results without an error.

### Default GET on activities returns minimal fields
A bare `GET /activities/{id}.json` returns only `id` and `etag`. You must explicitly pass `?fields=id,type,date,note,total,price,quantity,non_billable,...` to get anything useful.

### Address `name` is enum-validated
Must be exactly `"Work"`, `"Home"`, `"Billing"`, or `"Other"`. The natural-sounding `"Business"` returns 422. The `_normalize_address` helper auto-coerces invalid/missing names to `"Work"`.

### Mutating payloads must be wrapped
All POST/PATCH bodies must be `{"data": {...}}`. Sending the payload at root fails. The named tools handle this; the generic `clio_api_request` does NOT add the wrapper for you.

### Contact type discriminator
Use `"type": "Company"` for entities (Inc., LLC, Ltd., numbered companies) and `"type": "Person"` for individual humans. Both POST to `/contacts.json`.

### Refresh tokens may or may not rotate
Depends on the OAuth app config. The token manager handles both cases — if the refresh response includes a new `refresh_token`, it persists to `.clio_tokens.json`; if not, the .env value stays canonical. You don't need to reason about this in normal use.

### Access token TTLs vary
Some accounts get ~1 hour TTL, others get 30 days (`expires_in: 2592000`). The token manager honors whatever the server returns.

### Clio silently accepts unknown POST fields
Sending `{"data": {"client": {"id": X}, "description": "...", "zzz_bogus": 1}}` to `/matters.json` returns 201 with the unknown field dropped. **Validation errors are NOT a reliable way to discover valid field names** — you have to read the docs (or read this README).

### `/contacts.json` and `/matters.json` deletes are idempotent-ish
DELETE returns 204 on success. DELETE on an already-deleted resource returns 404. DELETE on a contact with open bills returns 409 with a specific error code; on a contact that's a client of an open matter returns 422.

### Bills are soft-deleted
DELETE on `/bills/{id}.json` returns 204 immediately, but the bill is moved to "void" state rather than purged from records. Probably an accounting/audit-trail design choice. Voided bills eventually drop off list endpoints.

### `practice_area_id` doesn't drive billing
You might think setting `practice_area_id` to a "small claims" area would make the matter flat-fee. It doesn't. Practice areas are pure metadata; they don't affect any billing fields.

## Architecture

The server is a single Python file (`clio_mcp_server.py`) using FastMCP from the [official Anthropic MCP SDK](https://github.com/modelcontextprotocol/python-sdk). It runs on demand (stdio mode for Claude Code / Claude Desktop / MCP Inspector) or as a long-lived HTTP service (for URL-based connectors).

The token manager (`ClioTokenManager`) handles OAuth refresh transparently — every API call checks the cached access token, refreshes if expired (with a 60s safety buffer), and persists rotated refresh tokens to `.clio_tokens.json` (chmod 600).

All tools return a uniform `{"status_code": int, "body": <parsed JSON>}` shape so error payloads from Clio's validator come through verbatim — useful when something doesn't work as expected.

## Contributing

PRs welcome, especially for:
- Additional tools (bills, trust requests, calendar entries, document uploads, etc.)
- Confirmed quirks I haven't documented
- Region/locale support
- Tests (the empirical findings would benefit from a recorded-cassette test suite)

If you discover Clio API behavior that contradicts what's documented here, please open an issue with a reproduction.

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgements

Built from frustration with the official documentation. The flat-fee workaround in particular took several hours of empirical testing to uncover — written up in [docs/flat-fee-workaround.md](docs/flat-fee-workaround.md) so the next person doesn't have to.

By [@BitGrateful](https://x.com/BitGrateful).
