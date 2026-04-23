"""
clio-mcp — A Model Context Protocol server for Clio Manage.

Exposes the Clio Manage v4 REST API as MCP tools so Claude (Desktop, Cowork,
Code, or any MCP-compatible client) can read and write Clio data: contacts,
matters, activities.

Includes a documented workaround for Clio's silent rejection of `billing_method`
on POST /matters.json — see docs/flat-fee-workaround.md and the
`clio_create_flat_fee_activity` tool.

Built with FastMCP from the official Anthropic MCP SDK
(`pip install "mcp[cli]"`).

License: MIT
Repo:    https://github.com/Lawyered0/clio-mcp
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env from the script directory so cwd doesn't matter when the host
# (Claude Desktop, Cowork, etc.) launches the server from an arbitrary dir.
_SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(_SCRIPT_DIR / ".env")


# ---------- Configuration ----------

# Default to US. For other regions, set CLIO_BASE_URL and CLIO_TOKEN_URL
# explicitly in .env. Known hosts:
#   US: app.clio.com
#   CA: ca.app.clio.com
#   EU/UK: eu.app.clio.com
#   AU: au.app.clio.com
# Mixing region endpoints in a single request/response cycle (e.g. minting a
# token at one region and calling the API at another) returns 401 invalid_token.
_DEFAULT_HOST = "app.clio.com"
CLIO_BASE_URL = os.getenv(
    "CLIO_BASE_URL", f"https://{_DEFAULT_HOST}/api/v4/"
).rstrip("/") + "/"
CLIO_TOKEN_URL = os.getenv(
    "CLIO_TOKEN_URL", f"https://{_DEFAULT_HOST}/oauth/token"
)

CLIO_CLIENT_ID = os.getenv("CLIO_CLIENT_ID")
CLIO_CLIENT_SECRET = os.getenv("CLIO_CLIENT_SECRET")

# Optional: if set, used as the default responsible_attorney + originating_attorney
# on clio_create_matter when the caller doesn't override. Required if the caller
# doesn't pass attorney_id explicitly.
_attorney_id_env = os.getenv("CLIO_DEFAULT_ATTORNEY_ID")
CLIO_DEFAULT_ATTORNEY_ID: Optional[int] = (
    int(_attorney_id_env) if _attorney_id_env else None
)

TOKEN_FILE = _SCRIPT_DIR / ".clio_tokens.json"

# Clio's contact address `name` field is enum-validated; "Business" returns 422.
VALID_ADDRESS_NAMES = {"Work", "Home", "Billing", "Other"}


# ---------- Token manager ----------

class ClioTokenManager:
    """Manages OAuth access tokens with auto-refresh and refresh-token rotation.

    Clio access tokens have varying TTLs by region/account; this manager reads
    `expires_in` from each refresh response and caches accordingly. Refresh
    tokens MAY rotate on refresh (depends on the OAuth app config); when they
    do, the new one is persisted to .clio_tokens.json and used in preference
    to the .env value on subsequent reads. If your account does not rotate
    refresh tokens, .clio_tokens.json simply never gets written.
    """

    def __init__(self) -> None:
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0  # epoch seconds
        self._refresh_token: Optional[str] = self._load_refresh_token()

    def _load_refresh_token(self) -> Optional[str]:
        # Prefer the file-stored token (it may be newer than .env post-rotation).
        if TOKEN_FILE.exists():
            try:
                stored = json.loads(TOKEN_FILE.read_text())
                token = stored.get("refresh_token")
                if token:
                    return token
            except (OSError, json.JSONDecodeError):
                pass
        return os.getenv("CLIO_REFRESH_TOKEN")

    def _save_refresh_token(self, refresh_token: str) -> None:
        TOKEN_FILE.write_text(json.dumps({"refresh_token": refresh_token}, indent=2))
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass  # best-effort; not critical for single-user local install.

    def get_access_token(self) -> str:
        # 60s safety buffer so we don't hand out a token about to expire.
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        return self._refresh()

    def _refresh(self) -> str:
        if not (CLIO_CLIENT_ID and CLIO_CLIENT_SECRET and self._refresh_token):
            raise RuntimeError(
                "Missing Clio OAuth credentials. Set CLIO_CLIENT_ID, "
                "CLIO_CLIENT_SECRET, and CLIO_REFRESH_TOKEN in .env "
                "(or store a refresh_token in .clio_tokens.json from a prior run). "
                "See docs/oauth-setup.md for the first-time authorization flow."
            )

        resp = httpx.post(
            CLIO_TOKEN_URL,
            data={
                "client_id": CLIO_CLIENT_ID,
                "client_secret": CLIO_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Clio token refresh failed ({resp.status_code}): {resp.text}\n"
                "Refresh token is likely expired or revoked. Re-run the OAuth "
                "authorization-code flow (see docs/oauth-setup.md), update "
                "CLIO_REFRESH_TOKEN in .env, then delete .clio_tokens.json."
            )

        body = resp.json()
        self._access_token = body["access_token"]
        self._expires_at = time.time() + int(body.get("expires_in", 3600))

        new_refresh = body.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            self._save_refresh_token(new_refresh)

        return self._access_token


_tokens = ClioTokenManager()


# ---------- API client ----------

def _clio_request(
    method: str,
    path: str,
    body: Optional[dict[str, Any]] = None,
    query: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Make an authenticated Clio API call. Always returns {status_code, body}."""
    url = CLIO_BASE_URL + path.lstrip("/")
    headers = {
        "Authorization": f"Bearer {_tokens.get_access_token()}",
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    resp = httpx.request(
        method.upper(),
        url,
        headers=headers,
        params=query,
        json=body,
        timeout=30.0,
    )

    if resp.status_code == 204 or not resp.text:
        parsed: Any = None
    else:
        try:
            parsed = resp.json()
        except json.JSONDecodeError:
            parsed = {"raw": resp.text}

    return {"status_code": resp.status_code, "body": parsed}


def _normalize_address(address: dict[str, Any]) -> dict[str, Any]:
    """Force address.name to a Clio-accepted enum value.

    Clio rejects address names other than Work/Home/Billing/Other. The
    natural-sounding "Business" returns 422. We auto-coerce invalid or missing
    values to "Work" so callers don't have to remember.
    """
    addr = dict(address)
    if addr.get("name") not in VALID_ADDRESS_NAMES:
        addr["name"] = "Work"
    return addr


def _resolve_attorney_id(explicit: Optional[int]) -> int:
    aid = explicit or CLIO_DEFAULT_ATTORNEY_ID
    if aid is None:
        raise ValueError(
            "No attorney_id provided and CLIO_DEFAULT_ATTORNEY_ID is not set "
            "in .env. Either pass attorney_id to the tool or set the env var."
        )
    return int(aid)


# ---------- MCP server ----------

mcp = FastMCP("clio")


@mcp.tool()
def clio_who_am_i() -> dict[str, Any]:
    """Return the authenticated Clio user's profile.

    Use this as the canonical "is auth working?" check before running any
    other tool. A 401 here means the refresh-token chain is broken — re-run
    the OAuth flow (docs/oauth-setup.md) and delete .clio_tokens.json.

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        body["data"] has id, name, email, and other Clio user fields.
    """
    return _clio_request("GET", "users/who_am_i.json")


@mcp.tool()
def clio_create_company_contact(
    name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    website: Optional[str] = None,
    address: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a Company contact in Clio (type=Company).

    Use for entity clients — corporations, LLCs, sole proprietorships
    operating under a business name. For individual humans, use
    clio_create_person_contact.

    Args:
        name: Company legal name (required).
        email: Primary email address.
        phone: Primary phone number.
        website: Company website URL.
        address: Optional dict with keys:
            - name: must be one of "Work", "Home", "Billing", "Other".
              Defaults to "Work" if missing or invalid.
              ("Business" is a known 422 trigger; auto-coerced to "Work".)
            - street, city, province, postal_code, country.

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        On success (201), body["data"] contains the created contact incl. id.
        On failure, body contains Clio's error payload verbatim.
    """
    contact: dict[str, Any] = {"type": "Company", "name": name}

    if email:
        contact["email_addresses"] = [
            {"name": "Work", "address": email, "default_email": True}
        ]
    if phone:
        contact["phone_numbers"] = [
            {"name": "Work", "number": phone, "default_number": True}
        ]
    if website:
        contact["web_sites"] = [{"name": "Work", "address": website}]
    if address:
        contact["addresses"] = [_normalize_address(address)]

    return _clio_request("POST", "contacts.json", body={"data": contact})


@mcp.tool()
def clio_create_person_contact(
    first_name: str,
    last_name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a Person contact in Clio (type=Person).

    Use for individual human clients. For business entities, use
    clio_create_company_contact.

    Args:
        first_name: Given name (required).
        last_name: Family name (required).
        email: Primary email address.
        phone: Primary phone number.
        address: Optional dict, same shape as clio_create_company_contact.

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        On success (201), body["data"] contains the created contact incl. id.
    """
    contact: dict[str, Any] = {
        "type": "Person",
        "first_name": first_name,
        "last_name": last_name,
    }

    if email:
        contact["email_addresses"] = [
            {"name": "Work", "address": email, "default_email": True}
        ]
    if phone:
        contact["phone_numbers"] = [
            {"name": "Work", "number": phone, "default_number": True}
        ]
    if address:
        contact["addresses"] = [_normalize_address(address)]

    return _clio_request("POST", "contacts.json", body={"data": contact})


@mcp.tool()
def clio_create_matter(
    client_id: int,
    description: str,
    open_date: Optional[str] = None,
    billing_method: str = "hourly",
    billable: bool = True,
    practice_area_id: Optional[int] = None,
    status: str = "open",
    attorney_id: Optional[int] = None,
    originating_attorney_id: Optional[int] = None,
) -> dict[str, Any]:
    """Create a Matter in Clio.

    Sets responsible_attorney (and originating_attorney if not specified
    separately) to attorney_id, falling back to CLIO_DEFAULT_ATTORNEY_ID
    from .env. Raises if neither is set.

    KNOWN LIMITATION (confirmed empirically against the live API):
    Clio's REST API silently ignores billing_method on POST /matters.json.
    No matter what value you send ("flat", "Flat", "FlatRate", "flat_fee",
    integers, etc.), the matter is saved as "hourly" when GETted back.
    PATCH after creation also returns 200 but doesn't change the value.
    Companion fields (flat_rate_amount, rate, billing_preference, ~16
    others) are all silently ignored too. The Clio web UI uses a private
    endpoint not exposed in the public REST API.

    WORKAROUND: leave matters as "hourly" and add a flat-fee Activity via
    clio_create_flat_fee_activity. Bills generated from the matter will
    total to the activity amount. See docs/flat-fee-workaround.md.

    Args:
        client_id: Clio contact id of the client (required). Look up via
            clio_find_contact if needed.
        description: Matter description (required).
        open_date: ISO date YYYY-MM-DD. Defaults to today.
        billing_method: Sent to Clio but currently ignored (see above).
            Defaults to "hourly" since that's what Clio will store regardless.
        billable: True (default) for billable matters.
        practice_area_id: Optional Clio practice area id.
        status: "open" (default), "pending", or "closed".
        attorney_id: Clio user id for responsible_attorney. Falls back to
            CLIO_DEFAULT_ATTORNEY_ID env var if omitted.
        originating_attorney_id: Defaults to the same as attorney_id.

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        On success (201), body["data"] contains the created matter.
    """
    aid = _resolve_attorney_id(attorney_id)
    oaid = originating_attorney_id or aid

    matter: dict[str, Any] = {
        "client": {"id": client_id},
        "description": description,
        "open_date": open_date or date.today().isoformat(),
        "billing_method": billing_method,
        "billable": billable,
        "status": status,
        "responsible_attorney": {"id": aid},
        "originating_attorney": {"id": oaid},
    }
    if practice_area_id is not None:
        matter["practice_area"] = {"id": practice_area_id}

    return _clio_request("POST", "matters.json", body={"data": matter})


@mcp.tool()
def clio_create_flat_fee_activity(
    matter_id: int,
    amount: float,
    description: str,
    entry_date: Optional[str] = None,
    activity_type: str = "ExpenseEntry",
) -> dict[str, Any]:
    """Add a flat-fee billable line item to a matter.

    The supported way to do flat-fee billing via the Clio v4 API. Since
    POST /matters.json silently ignores billing_method (see clio_create_matter
    for details), the matter stays labeled "hourly" but the actual billing
    math comes from the activities on it. A bill generated from the matter
    will total to the sum of its billable activities — including this
    flat-amount line item.

    DEFAULT IS ExpenseEntry, NOT TimeEntry. Confirmed empirically: TimeEntry
    via the API computes total as `quantity_in_hours * rate`, so a 0-hour
    flat fee produces total=$0 regardless of the `price` field. ExpenseEntry
    computes total as `quantity * price`, so qty=1 with price=<amount>
    correctly gives total=<amount>.

    Args:
        matter_id: Clio matter id to attach the activity to (required).
        amount: Flat fee in dollars, e.g. 595.00 (required).
        description: Line-item text shown on the bill, e.g. "Flat fee:
            initial consultation" (required). Stored as 'note' in Clio's
            activity schema — both 'description' and 'note' are accepted on
            POST but Clio normalizes to 'note' on GET.
        entry_date: ISO date YYYY-MM-DD for the activity. Defaults to today.
        activity_type: "ExpenseEntry" (default; produces correct total via
            qty=1 * price=amount) or "TimeEntry" (only produces non-zero
            totals if you also set quantity_in_hours and rate appropriately
            — via this tool, TimeEntry will always total $0 because
            quantity_in_hours is set to 0).

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        On success (201), body["data"] contains the created activity's id.
        Verify with clio_api_request("GET", f"activities/{id}.json",
        query={"fields": "id,type,date,note,total,price,quantity,non_billable,matter{id,display_number}"})
        and confirm `total == amount`.
    """
    payload: dict[str, Any] = {
        "type": activity_type,
        "matter": {"id": matter_id},
        "date": entry_date or date.today().isoformat(),
        "note": description,
        "price": amount,
        "non_billable": False,
    }
    if activity_type == "TimeEntry":
        payload["quantity_in_hours"] = 0
    else:
        payload["quantity"] = 1

    return _clio_request("POST", "activities.json", body={"data": payload})


@mcp.tool()
def clio_find_contact(
    query: Optional[str] = None,
    email: Optional[str] = None,
    contact_type: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search Clio contacts by name and/or email.

    Args:
        query: Free-text substring search across contact names.
        email: Filter to contacts with this exact email address.
        contact_type: "Person" or "Company" to filter by type.
        limit: Max results (default 20, Clio cap is 200).

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        body["data"] is the array of matches (id, name, type, primary email/phone).
    """
    params: dict[str, Any] = {
        "limit": limit,
        "fields": (
            "id,name,first_name,last_name,type,"
            "primary_email_address,primary_phone_number"
        ),
    }
    if query:
        params["query"] = query
    if email:
        params["email"] = email
    if contact_type:
        params["type"] = contact_type

    return _clio_request("GET", "contacts.json", query=params)


@mcp.tool()
def clio_find_matter(
    display_number: Optional[str] = None,
    query: Optional[str] = None,
    client_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search Clio matters.

    Args:
        display_number: Exact match on Clio's display_number (e.g. "00123").
        query: Free-text search across matter description and display number.
        client_id: Filter to matters belonging to this contact id.
        status: "open", "pending", or "closed".
        limit: Max results (default 20).

    Returns:
        {"status_code": int, "body": <parsed JSON>}.
        body["data"] is the array with id, display_number, description,
        status, billing_method, billable, client, open_date.
    """
    params: dict[str, Any] = {
        "limit": limit,
        "fields": (
            "id,display_number,description,status,billing_method,billable,"
            "client{id,name},open_date"
        ),
    }
    if display_number:
        params["display_number"] = display_number
    if query:
        params["query"] = query
    if client_id:
        params["client_id"] = client_id
    if status:
        params["status"] = status

    return _clio_request("GET", "matters.json", query=params)


@mcp.tool()
def clio_delete_matter(matter_id: int) -> dict[str, Any]:
    """Delete a Matter from Clio. Useful for cleaning up test data.

    DESTRUCTIVE — the matter is gone, including any time entries, notes, or
    documents on it. Bills attached to the matter are NOT auto-deleted (they
    just lose their matter link); delete those separately if needed.

    Args:
        matter_id: Clio matter id to delete.

    Returns:
        {"status_code": int, "body": null}. 204 indicates success.
    """
    return _clio_request("DELETE", f"matters/{matter_id}.json")


@mcp.tool()
def clio_delete_contact(contact_id: int) -> dict[str, Any]:
    """Delete a Contact from Clio. Useful for cleaning up test data.

    DESTRUCTIVE. Will fail (likely 422 or 409) if the contact is the client
    on any matter or has open bills — delete those first if needed.

    Args:
        contact_id: Clio contact id to delete.

    Returns:
        {"status_code": int, "body": null}. 204 indicates success.
    """
    return _clio_request("DELETE", f"contacts/{contact_id}.json")


@mcp.tool()
def clio_api_request(
    method: str,
    path: str,
    body: Optional[dict[str, Any]] = None,
    query: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Generic escape hatch for any Clio v4 API call.

    Use when the named tools don't cover the endpoint. Examples:
      - GET users/who_am_i.json - returns the authenticated user
      - GET practice_areas.json - lists practice areas
      - PATCH matters/123.json with body {"data": {"description": "new"}}
      - GET activities.json?matter_id=123 - lists activities on matter 123

    Args:
        method: HTTP method - "GET", "POST", "PATCH", or "DELETE".
        path: Path relative to base URL, e.g. "matters/123.json". No leading
            slash, no base URL.
        body: Request body. For mutating calls, wrap your payload as
            {"data": {...}} - Clio requires this. The wrapper is NOT added
            automatically here.
        query: Query string params dict.

    Returns:
        {"status_code": int, "body": <parsed JSON>} verbatim from Clio.
    """
    return _clio_request(method, path, body=body, query=query)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Clio MCP Server")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport (for Claude Desktop, Claude Code, mcp dev). "
             "Default is streamable-http for use as a URL-based connector.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("CLIO_MCP_HOST", "127.0.0.1"),
        help="HTTP bind host (ignored in stdio mode). Default 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CLIO_MCP_PORT", "8765")),
        help="HTTP bind port (ignored in stdio mode). Default 8765.",
    )
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # Hosts that connect via URL: http://<host>:<port>/mcp
        mcp.run(transport="streamable-http")
