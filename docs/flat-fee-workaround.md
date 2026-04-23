# The flat-fee matter workaround

> **TL;DR:** Clio's REST API silently ignores `billing_method` on `POST /matters.json`. Every value gets saved as `"hourly"`. The only way to bill flat-fee via the API is to leave the matter as `"hourly"` and add a flat-amount Activity. Use `clio_create_flat_fee_activity` (it's an `ExpenseEntry` under the hood — `TimeEntry` with 0 hours always totals $0).

## Why this exists

If you naively try:

```python
clio_create_matter(
    client_id=123,
    description="Demand letter",
    billing_method="flat",   # <-- you'd think this works
)
```

Clio returns 201 Created. Looks fine. Then you `GET /matters/<id>.json?fields=billing_method` and get back:

```json
{"data": {"billing_method": "hourly"}}
```

Your `"flat"` was silently dropped. No warning, no error.

## What I tested (so you don't have to)

Every variant of `billing_method`:
- `"flat"` → saved as `"hourly"`
- `"Flat"` → saved as `"hourly"`
- `"FLAT"` → saved as `"hourly"`
- `"FlatRate"` → saved as `"hourly"`
- `"flat_fee"` → saved as `"hourly"`
- `"contingency"` → saved as `"hourly"`
- `"Contingency"` → saved as `"hourly"`
- `"hourly"` → saved as `"hourly"`
- Integer `2`, `3` → saved as `"hourly"`

PATCH after creation also fails silently:

```http
PATCH /matters/<id>.json
{"data": {"billing_method": "flat"}}

→ 200 OK
→ GET shows billing_method: "hourly" still
```

Companion fields I tried (none affect the outcome):
- `flat_rate_amount`, `flat_rate`, `flat_rate_cost`
- `flat_fee_amount`, `flat_fee`
- `fee`, `fee_amount`, `quoted_fee`
- `rate` (object), `rate_type`
- `matter_rate`, `matter_rates`, `custom_rates`
- `billing_preference`, `billing_type`
- `default_rate`

All silently dropped. All matters still saved as `"hourly"`.

## Why does the Clio web UI show flat-fee matters then?

It does — and they DO show up correctly via `GET` if you query existing matters in your account. So the field is settable somewhere; it's just not the public REST API. The Clio web UI almost certainly uses an internal/private endpoint that isn't exposed in v4.

I confirmed this by emailing api@clio.com — got back a tier-1 marketing reply pointing me at the same v4 docs that don't document any flat-fee setter. Escalation didn't go anywhere useful before I found the workaround below.

If you have a contact at Clio who can confirm/deny this and point at a real solution, please open an issue.

## The workaround

A matter in Clio is just a container. The actual billing math comes from the **activities** (time entries, expense entries, hard cost entries) attached to the matter. A bill generated from the matter totals to the sum of its billable activities — regardless of the matter-level `billing_method` label.

So: leave the matter as `"hourly"` (you have no choice), and add a flat-amount activity for the quoted fee.

```python
# Step 1: Create the matter (will save as "hourly" no matter what you send)
matter = clio_create_matter(
    client_id=123,
    description="Demand letter",
)
matter_id = matter["body"]["data"]["id"]

# Step 2: Add the flat-fee activity
clio_create_flat_fee_activity(
    matter_id=matter_id,
    amount=595.00,
    description="Flat fee: demand letter",
)
```

Now generate a bill on that matter (via Clio UI or API) and it'll total $595.00. The matter still labels as "hourly" in reports — cosmetic only. You can also one-click toggle the matter to "Flat fee" in the Clio UI's matter settings if you want it to display correctly there; that's out of scope for the API.

## Why ExpenseEntry, not TimeEntry

`clio_create_flat_fee_activity` defaults to `activity_type="ExpenseEntry"`. This is deliberate — and is the second non-obvious thing this doc exists to tell you.

Activity total math in Clio:

| Type | Total formula |
|---|---|
| `TimeEntry` | `quantity_in_hours × rate` |
| `ExpenseEntry` | `quantity × price` |

If you naively create a `TimeEntry` with `quantity_in_hours=0` and `price=595`, the total comes out to **$0.00**, because TimeEntry doesn't use the `price` field for math — it uses `rate`. A 0-hour TimeEntry with no rate set is always $0.

`ExpenseEntry` with `quantity=1` and `price=595` correctly produces `total=$595.00`. Hence the default.

(You CAN make a TimeEntry produce a non-zero total — set `quantity_in_hours=1` and `rate=595`, getting "1 hour at $595/hour = $595". But that's misleading on bills since it doesn't represent what the work was, and the existing flat-fee TimeEntries on Clio accounts that I've seen are likely created via the UI which uses different mechanics.)

## Caveats

1. **The matter still says "hourly" in Clio's UI and reports.** Annoying but cosmetic. The bill amount is correct.
2. **No "quoted fee" tracking at the matter level.** Clio's flat-fee feature normally lets you track "quoted $X, billed $Y" — you don't get that via this workaround. If you need it, store the quoted fee in a custom field on the matter.
3. **Trust requests work.** They're tied to bills, not to the matter's `billing_method`.
4. **If Clio ever fixes this**, `clio_create_flat_fee_activity` becomes obsolete (or at least optional). Update `clio_create_matter`'s docstring and consider deprecating the workaround tool.
