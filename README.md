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
git clone https://github.com/<your-username>/clio-mcp.git
cd clio-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Get OAuth credentials from Clio (one-time)
#    See docs/oauth-setup.md for the authorization-code flow walkthrough.
#    You'll end up with: CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN.

# 3. Configure
cp .env.example .env
# edit .env with your credentials and (optional) default attorney id

# 4. Test it works (Ctrl+C to exit)
python clio_mcp_server.py --stdio
# Or run as an HTTP service for URL-based connectors:
python clio_mcp_server.py            # binds 127.0.0.1:8765 by default

# 5. Wire it into your MCP client of choice
#    - Claude Code CLI:        edit ~/.claude/settings.json (see below)
#    - Claude Desktop:         build a DXT (see docs/claude-desktop-dxt.md)
#    - Cowork / claude.ai web: needs HTTPS — use a tunnel like Cloudflare Tunnel
#    - MCP Inspector:          mcp dev clio_mcp_server.py
```

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
