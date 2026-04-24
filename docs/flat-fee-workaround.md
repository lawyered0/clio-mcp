# Flat-fee matter setup

> **TL;DR:** The `billing_method` field at the matter root is silently ignored on POST/PATCH. But flat fee **is** settable via a nested `custom_rate` association — PATCH `matters/{id}.json` with `{"data": {"custom_rate": {"type": "FlatRate", "rates": [{"user": {"id": <attorney>}, "rate": <amount>}]}}}` and Clio (a) flips `billing_method` to `"flat"` and (b) auto-creates a billable `flat_rate: true` TimeEntry whose total equals the amount. That's the one-step, Clio-native setup. The tool `clio_create_matter` takes an optional `flat_rate_amount` parameter that does this for you.

## History

The title of this file still says "workaround" for historical reasons. Early versions of this server couldn't figure out how to set flat-fee billing via the public v4 API — the natural-looking field `billing_method: "flat"` is silently dropped on POST, and every companion field guess (`flat_rate_amount`, `flat_rate`, `rate`, `matter_rate`, `billing_preference`, etc.) is also ignored. A workaround was adopted: leave matters as `"hourly"` and attach a flat-amount `ExpenseEntry` for the fee. Bills generated from the matter would total to the activity amount, but the matter-level label still read `"hourly"` in reports.

Clio Support later pointed at the **Matters > Associations > Custom Rates** section of the v4 docs. Testing confirmed that `custom_rate` as a nested association works where `billing_method` at the root does not. This doc has been updated to reflect the correct path; the ExpenseEntry workaround is preserved below for cases where it's still useful (add-on charges, post-hoc line items).

## The correct path: `custom_rate` PATCH

```python
# Step 1: Create the matter
response = clio_create_matter(
    client_id=123,
    description="Demand letter",
    flat_rate_amount=595.00,   # <-- the key
)
```

Under the hood, `clio_create_matter` with `flat_rate_amount` set does:

```http
POST /matters.json
{"data": {"client": {"id": 123}, "description": "Demand letter", ...}}
→ 201 Created, matter id=999

PATCH /matters/999.json
{"data": {"custom_rate": {
  "type": "FlatRate",
  "rates": [{"user": {"id": <attorney>}, "rate": 595.00}]
}}}
→ 200 OK
```

Then `GET /matters/999.json?fields=billing_method,custom_rate` returns:

```json
{
  "data": {
    "billing_method": "flat",
    "custom_rate": {
      "type": "FlatRate",
      "rates": [
        {
          "id": 123456,
          "rate": 595.00,
          "user": {"id": <attorney>, "name": "..."},
          "activity_description": null
        }
      ]
    }
  }
}
```

Separately, a billable `TimeEntry` is auto-created with `flat_rate: true`, `price: 595`, `total: 595`, `billed: false`. You can verify with `GET /activities.json?matter_id=999&fields=id,type,note,price,total,flat_rate,billed`. That's the line item the bill will pick up.

`activity_description` inside the rate is optional — pass it as `null` (or omit) if you don't have a specific activity description to tie the rate to. For contingency fees, use `type: "ContingencyFee"` with `rate: <percentage>` (no `activity_description`).

### Gotcha: updating the rate after creation

A second PATCH on the same matter **without** including the existing rate's `id` creates a NEW rate entry and leaves the old auto-generated TimeEntry as an orphan (both will appear on bills). To update cleanly, first `GET /matters/{id}.json?fields=custom_rate` to find the existing rate's `id`, then PATCH with that id plus the new `rate` value — this updates in place:

```http
PATCH /matters/999.json
{"data": {"custom_rate": {
  "type": "FlatRate",
  "rates": [{"id": <existing rate id>, "rate": 750.00}]
}}}
```

To delete the rate entirely, use `_destroy: true` on the rate object (per Clio's docs on association updates).

### Empirical data that was tested before finding `custom_rate`

Every variant of the top-level `billing_method` field — `"flat"`, `"Flat"`, `"FLAT"`, `"FlatRate"`, `"flat_fee"`, `"contingency"`, `"Contingency"`, `"hourly"`, integer `2`, `3` — all save as `"hourly"` on POST. PATCH after creation returns 200 OK but never changes the stored value. Companion field guesses that were also silently dropped: `flat_rate_amount`, `flat_rate`, `flat_rate_cost`, `flat_fee_amount`, `flat_fee`, `fee`, `fee_amount`, `quoted_fee`, `rate` (object), `rate_type`, `matter_rate`, `matter_rates`, `custom_rates` (plural), `billing_preference`, `billing_type`, `default_rate`. None of these are the setter. Only the nested `custom_rate` association works.

## The legacy ExpenseEntry workaround (still useful for add-on charges)

`clio_create_flat_fee_activity` is still in the tool set. It creates an `ExpenseEntry` with `quantity=1, price=<amount>` (giving `total=<amount>`). Do **not** use it for the primary flat fee — that's what `flat_rate_amount` on `clio_create_matter` is for, and doubling up would produce two billable line items.

**When to use `clio_create_flat_fee_activity`:**

- **Add-on charges** on top of the flat fee: credit-card processing surcharges, disbursements, filing fees, per-letter overages on capped matters, etc.
- **Matters created without `flat_rate_amount`** where the fee wasn't known upfront and you later want to drop in a fixed amount without reconfiguring the rate. (If you want the matter to actually show as flat in reports, prefer a follow-up PATCH with `custom_rate` instead — but the ExpenseEntry path is fine if you just need the bill to total correctly.)

### Why ExpenseEntry, not TimeEntry, for manual flat-amount line items

A manual TimeEntry via the public API computes `total = quantity_in_hours × rate`, NOT `× price`. So a TimeEntry with `quantity_in_hours: 0` always totals $0 regardless of `price`. ExpenseEntry computes `total = quantity × price`, so `qty=1, price=N → total=N`. That's why `clio_create_flat_fee_activity` defaults to `ExpenseEntry`.

(Clio's auto-generated flat_rate TimeEntry from the `custom_rate` association is a different mechanism — it sets `flat_rate: true`, which bypasses the quantity multiplication and uses the rate value directly. That's why it works when a manually-constructed TimeEntry wouldn't.)

## Caveats

1. **Changing the rate after creation requires care** — include the existing rate's `id` in the PATCH to update in place, otherwise a duplicate rate + orphan TimeEntry are created. See the gotcha section above.
2. **`activity_description` is optional.** If you want the matter's flat fee tied to a named activity description in Clio, pass `activity_description: {"id": <activity_description_id>}` inside the rate object. If you don't need that, pass `null` or omit — the rate works fine either way.
3. **Trust requests** are tied to bills, not to the matter's `billing_method`. They work identically under hourly, flat, and contingency configurations.
4. **Contingency fees** use the same mechanism: PATCH `custom_rate` with `type: "ContingencyFee"` and `rate: <percentage>` (decimal, e.g. `20` for 20%). No `activity_description` is valid for contingency.
