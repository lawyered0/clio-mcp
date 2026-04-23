# Clio v4 API quirks

A field guide to non-obvious behavior of Clio Manage's v4 REST API, discovered empirically over many hours of testing. These are things the official docs don't tell you that will cost you time.

If you're building anything against Clio's API — not just MCP servers — read this first.

---

## Region routing

Clio runs on regional hosts. Tokens minted at one region don't authenticate against another region's API; you'll get 401 invalid_token. Pick one host and stick with it for both OAuth and API calls.

| Region | Host |
|---|---|
| US | `app.clio.com` |
| CA | `ca.app.clio.com` |
| EU/UK | `eu.app.clio.com` |
| AU | `au.app.clio.com` |

Anecdotally, the US host sometimes accepts API calls with tokens minted at non-US regions and routes them through, but this is undocumented behavior and not reliable. Mixing endpoints in one cycle is a guaranteed 401.

---

## Mutating payloads must be wrapped

All POST/PATCH bodies must be `{"data": {...}}`:

```json
POST /matters.json
{"data": {"client": {"id": 1}, "description": "..."}}    ✓ works
{"client": {"id": 1}, "description": "..."}              ✗ fails
```

This is consistent across every mutating endpoint.

---

## Clio silently accepts unknown fields

Sending unrecognized keys in a POST/PATCH body returns 201/200 with the unknown fields silently dropped. **Validation errors are NOT a reliable way to discover valid field names** — you have to read the docs (and even those are incomplete).

```json
POST /matters.json
{"data": {"client": {"id": 1}, "description": "...", "zzz_bogus": 1}}
→ 201 Created (zzz_bogus dropped)
```

Implication: if a field you're sending isn't taking effect, you can't tell from the response. You have to GET the resource back and see what stuck.

---

## `billing_method` on /matters.json is silently ignored

The big one. Detailed in [flat-fee-workaround.md](flat-fee-workaround.md). Short version: every value sent for `billing_method` saves as `"hourly"`. PATCH after creation fails the same way silently. The Clio web UI uses an internal endpoint not exposed in v4. Workaround: leave matters as `"hourly"` and use a flat-amount Activity for the actual fee.

---

## Address `name` is enum-validated

Contact addresses have a `name` field for the type label. It must be exactly one of:

```
Work, Home, Billing, Other
```

`"Business"` returns 422. So do casing variants like `"work"`. The validator is strict about both whitelist membership and casing.

---

## Contact type discriminator

Contacts have a `type` field that determines the schema. Use:

- `"type": "Company"` for entities (Inc., LLC, Ltd., numbered companies). Uses the `name` field.
- `"type": "Person"` for individual humans. Uses `first_name` + `last_name`, NOT `name`.

Both POST to the same endpoint `/contacts.json`.

---

## Activity field-name aliases

The `description` and `note` fields on activities are an annoying tarpit:

- POST accepts both `description` and `note` interchangeably for the line-item text.
- GET only accepts `note` in `?fields=...`. Querying `description` returns 400 InvalidFields.
- Internally, Clio stores it as `note`.

Just always send and request `note` — saves the confusion.

---

## TimeEntry total math

`TimeEntry.total = quantity_in_hours × rate`, NOT `× price`. So a TimeEntry with `quantity_in_hours: 0` always totals $0 regardless of `price`.

For flat-fee billing line items, use `ExpenseEntry` instead — its math is `quantity × price`, so `qty=1, price=N → total=N`.

---

## Activities — invalid fields

These are NOT valid fields when querying activities (return 400):
- `rate` (use `price` and `quantity` instead)
- `description` (use `note` — see above)

---

## `matter_id` filter footgun on /activities.json

The list-filter param is `matter_id` (singular int). Common typos:

- `?matter=123` → silently ignored, returns account-wide activities (no error)
- `?matter[id]=123` → silently ignored, returns account-wide activities
- `?matter_ids=123` → silently ignored, returns account-wide activities

Only `?matter_id=123` actually filters. The silent-fallback-to-no-filter behavior is the dangerous part.

---

## Default GET on activities is minimal

A bare `GET /activities/{id}.json` (no `?fields=...`) returns only `id` and `etag`. You must explicitly request fields to get anything useful:

```
GET /activities/123.json?fields=id,type,date,note,total,price,quantity,non_billable,billed,matter{id,display_number}
```

Same is true for matters — bare GET returns minimal fields, you have to ask for what you want.

---

## Refresh tokens may or may not rotate

Whether the OAuth refresh response includes a new `refresh_token` depends on the OAuth app's configuration. Both behaviors exist:

- **No rotation**: `/oauth/token` response has only `access_token` and `expires_in`. Your `.env` refresh token is the long-lived one.
- **Rotation**: response includes a new `refresh_token`; old one is invalidated immediately.

Build your token manager to handle both. If a new refresh token comes back, persist it; otherwise, the .env value stays canonical.

---

## Access token TTLs vary

Some accounts get ~1 hour TTL (`expires_in: 3600`), others get 30 days (`expires_in: 2592000`). Don't hardcode an assumption — read whatever the server returns.

---

## Bills are soft-deleted

`DELETE /bills/{id}.json` returns 204 immediately, but the bill is moved to `"void"` state rather than purged. Probably an accounting / audit trail design choice. Voided bills eventually drop off list endpoints (eventual consistency — there can be a brief lag where they still appear).

---

## Contact deletion has cascading constraints

`DELETE /contacts/{id}.json` fails if the contact:
- Is the client of any matter (open or closed) → 422
- Has any open bills → 409 with `"Client has open bills"`
- Has unbilled activities → 422

To fully purge: delete activities → delete matters → delete bills → delete contact.

---

## Practice areas don't drive billing

You might think setting `practice_area_id` to a "Small Claims" area would automatically make the matter flat-fee (since small claims work tends to be flat-fee in many practices). It doesn't. Practice areas are pure metadata; they don't affect any billing fields. Same outcome (`billing_method: "hourly"`) regardless of practice area.

---

## OAuth app redirect URI matching is exact

When exchanging an authorization code for tokens, the `redirect_uri` you send must EXACTLY match what was used in the `/oauth/authorize` call AND what's registered on the OAuth app — including trailing slash, port, and protocol. Mismatches return 400 `invalid_grant` with a generic error message that doesn't tell you it's the redirect URI.

---

## Field-selection nested syntax

You can request nested fields on related objects via brace syntax:

```
?fields=id,client{id,name},responsible_attorney{id,name}
```

Not all relations are queryable this way; if Clio returns 400 InvalidFields on a relation, drop that relation from the field spec.

---

## OPTIONS isn't supported

`OPTIONS /matters.json` (and other endpoints) return 404. So you can't introspect allowed methods that way.

---

## What I haven't verified but suspect

- **Webhooks**: I haven't tested Clio's webhook system through this server, so quirks there are unknown.
- **Document upload**: untested. The endpoint exists but the multipart format isn't well-documented in v4.
- **Trust requests**: untested.
- **Custom fields**: queryable via `custom_field_values{...}` field selection, but the create/update flow has its own quirks I haven't fully mapped.

PRs welcome with additions — especially anything you discover empirically that contradicts the above.
