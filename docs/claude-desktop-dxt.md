# Wiring clio-mcp into Claude Desktop (DXT)

Claude Desktop (the macOS app) loads MCP servers via DXT (Desktop Extensions). This is different from Claude Code's `~/.claude/settings.json` and from Cowork's URL-based connectors. Here's how to package this server as a DXT and install it.

## What a DXT is

A DXT is a directory containing `manifest.json` plus optional assets (icons, server code), zipped with a `.dxt` extension. Claude Desktop's "Install Extension from File" reads this and registers the server.

## Minimal manifest (thin wrapper, recommended)

The simplest pattern: bundle nothing, just point Claude Desktop at the actual server via absolute paths. The DXT becomes a 2 KB pointer; the actual code lives wherever you cloned the repo.

Save this as `manifest.json`:

```json
{
  "manifest_version": "0.3",
  "name": "clio-mcp",
  "display_name": "Clio MCP",
  "version": "1.0.0",
  "description": "MCP server for Clio Manage v4 API.",
  "author": {
    "name": "<your name>",
    "url": "https://github.com/<your-username>/clio-mcp"
  },
  "server": {
    "type": "python",
    "entry_point": "clio_mcp_server.py",
    "mcp_config": {
      "command": "/absolute/path/to/clio-mcp/.venv/bin/python",
      "args": [
        "/absolute/path/to/clio-mcp/clio_mcp_server.py",
        "--stdio"
      ],
      "env": {}
    }
  },
  "tools": [
    { "name": "clio_who_am_i",                "description": "Auth check." },
    { "name": "clio_create_company_contact",  "description": "Create entity contact." },
    { "name": "clio_create_person_contact",   "description": "Create individual contact." },
    { "name": "clio_create_matter",           "description": "Create matter." },
    { "name": "clio_create_flat_fee_activity","description": "Add flat-fee line item." },
    { "name": "clio_find_contact",            "description": "Search contacts." },
    { "name": "clio_find_matter",             "description": "Search matters." },
    { "name": "clio_delete_matter",           "description": "Delete matter (test cleanup)." },
    { "name": "clio_delete_contact",          "description": "Delete contact (test cleanup)." },
    { "name": "clio_api_request",             "description": "Generic API escape hatch." }
  ],
  "license": "MIT",
  "compatibility": {
    "platforms": ["darwin"]
  }
}
```

Replace `/absolute/path/to/clio-mcp/` with your actual install path. Both occurrences.

## Build the DXT

```bash
mkdir -p /tmp/clio-mcp-dxt
cp manifest.json /tmp/clio-mcp-dxt/
cd /tmp/clio-mcp-dxt
zip -r ~/Downloads/clio-mcp.dxt manifest.json
```

That's it. ~2 KB file.

## Install in Claude Desktop

1. Open Claude Desktop → **Settings → Extensions** (or wherever the install-from-file option lives in your version)
2. Drag `clio-mcp.dxt` onto the window, or use "Install from File"
3. Approve the unsigned-extension warning. (If your Claude Desktop has the DXT allowlist enabled, you may need to flip `dxt:allowlistEnabled` to `false` in `~/Library/Application Support/Claude/config.json` — single-user local installs of unsigned extensions typically need this.)

## ⚠️ Restart Claude Desktop fully (Cmd+Q)

**This is the part that catches everyone**, including me. Newly-installed DXTs don't appear in any session's tool registry until Claude Desktop is fully quit and relaunched. Closing the window isn't enough — `Cmd+Q` to actually exit, then relaunch.

After relaunch, in any new chat (or Code tab session): `do you have clio_who_am_i?` → should be yes.

## Why a thin wrapper instead of bundling

You CAN bundle the Python source inside the DXT and use `${__dirname}/clio_mcp_server.py` in the manifest's `args`, the way Anthropic's official PDF / Word DXTs do for their Node.js source. But:

- You'd still need a Python venv outside the DXT (Claude Desktop doesn't ship a Python runtime), so you're not actually self-contained
- Editing the source means rebuilding and reinstalling the DXT every time
- The thin-wrapper approach lets you `git pull && restart Claude Desktop` to update — much faster iteration

The downside is path fragility: if you move the repo, you have to rebuild the DXT. For a personal install, that's fine; for distribution, the bundled approach is more portable.

## Updating the server

Because the DXT just points at your local source via absolute path:

1. Edit `clio_mcp_server.py` in the repo
2. `Cmd+Q` Claude Desktop
3. Relaunch

That's the whole update flow. No DXT re-install.

## Architecture context

Claude Desktop has three independent surfaces in the same app:

| Surface | Tool source | This DXT works here? |
|---|---|---|
| Code tab (Claude Code embed) | DXT extensions | ✓ |
| Regular chat | DXT extensions | ✓ |
| Cowork | URL-based connectors | ✗ (different system) |

For the Cowork surface, you'd need to expose the server as an HTTPS endpoint (e.g. via Cloudflare Tunnel) and add it to Cowork's connector form. Cowork rejects plain HTTP and may also reject `127.0.0.1` URLs. That's a separate setup path; this doc only covers the DXT path which works for Code tab and regular chat.

## Troubleshooting

**Tool doesn't appear after install + restart:**
- Check `~/Library/Logs/Claude/mcp.log` for `[Clio MCP] Initializing server...` and any error after
- Check `~/Library/Logs/Claude/mcp-server-Clio MCP.log` for stderr from the Python process
- Verify the absolute paths in `manifest.json` actually exist
- Verify the venv has `mcp`, `httpx`, `python-dotenv` installed

**Server starts but `clio_who_am_i` returns 401:**
- `.env` is missing or has wrong credentials
- Wrong region — check that `CLIO_BASE_URL` and `CLIO_TOKEN_URL` point at the same regional host

**Server starts then immediately disconnects:**
- Normal at install time — Claude Desktop briefly launches the server to scan its tools, then disconnects until a session needs it. Look for the `tools/list` response in the log to confirm the server worked.
