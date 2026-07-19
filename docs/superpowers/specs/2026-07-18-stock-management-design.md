# Stock management — bot Stock tab + web Stock tab

## Goal

Give the admin one place (per surface) to see every product's real availability
and act on it in bulk, instead of drilling into each product's manage
screen/edit dialog one at a time. Two independent surfaces, one mirrored UX:

- **Telegram bot**: new top-level "📦 Stock" menu entry.
- **Web admin panel** (`admin/`, Next.js + Supabase): new "Stock" tab inside
  the existing `/products` page.

Both list every product sorted low-stock-first, support tap/click into a
type-aware quick action, and support multi-select bulk actions.

"Real availability" already means `effective_stock` in both codebases: a
keyed ("automatic") product's stock is its unused-delivery-code count, not
the manually-typed `stock` column; a manual/reseller product uses the typed
number as-is. Both surfaces already have this helper — this feature reuses
it, does not reintroduce a second source of truth.

## Bot: "📦 Stock" tab

### Entry point

Add a `📦 Stock` button to `main_menu_keyboard` (admin section, next to the
existing `📦 Products` button), callback `menu:stock`. `📦 Products` is
unchanged — it stays the place to edit name/price/description; Stock is
purely for availability triage.

### List (browse mode)

`stock_list(update, context, page=0)` in `products_admin.py`:

- Query: `db.list_products(only_active=False)` + `db.get_key_pool_counts()`
  (same pair the existing admin product list already uses).
- Compute `effective_stock` per product, sort: out-of-stock first, then
  low-stock (`<= LOW_STOCK_THRESHOLD`, already 3 in `keyboards.py`), then
  the rest — each group A→Z by name. Deactivated products sort last
  regardless of stock (their stock number is moot).
- Row label/icon: reuse `_catalog_label`-style formatting and
  `_product_icon_btn` (icon swaps to ❌ when out of stock, same as the
  existing admin product list) — callback `stock:row:<id>:<page>`.
- Page size 15, same `⬅️ Prev` / `Next ➡️` row as every other paginated list.
- Bottom row: `☑️ Select` (enter select mode) + `🔄 Refresh` + `⬅️ Menu`.

### Quick action sheet (tap a row in browse mode)

Type-aware, both variants end with `📄 Full product page` (→ existing
`manage:<id>`) and `⬅️ Back to Stock`:

- **Keyed product**: `➕ Add codes` (reuses the existing `keys:<id>` entry
  point verbatim — no new conversation), `🗑 Clear unused codes` (new, asks
  "Clear N unused code(s)? This can't be undone." before calling the new
  `db.clear_unused_keys(pid)`).
- **Manual product**: `✏️ Edit stock number` (reuses the existing
  `edit:stock:<id>` entry point verbatim).

### Select mode

`☑️ Select` re-renders the same list with:
- Each row toggles a ☑️/⬜ prefix on tap (`stock:tog:<id>:<page>`) instead of
  opening the quick-action sheet. Selection lives in
  `context.user_data["stock_selected"]` (a set of product ids) — cleared on
  entering/leaving select mode or on conversation fallback (`/cancel`,
  `/start`), same lifecycle as every other in-flight admin state in this
  codebase.
- Bottom bar (shown once ≥1 selected): `🚫 Deactivate` · `♻️ Reactivate` ·
  `🗑 Clear codes` · `✖️ Cancel select`. All three action buttons are always
  shown together rather than computed per-selection (simpler than
  conditionally hiding buttons); each handler silently skips selected
  products the action doesn't apply to (e.g. Deactivate skips already-
  inactive ones) and reports "Applied to N of M selected" — no dead-end if
  the admin's selection is mixed.
- Tapping an action shows one confirm step ("Deactivate 3 product(s)?"
  Yes/No) before applying — same pattern as the existing single-product
  delete confirmation (`delok:<pid>` / back in `products_admin.py`).
- Applying an action loops the existing single-product functions
  (`db.set_product_active`, new `db.clear_unused_keys`) — no new bulk SQL,
  selection sizes here are small (admin-picked, not query-driven).

### New DB function

`app/db/products.py`:

```python
async def clear_unused_keys(product_id: int) -> int:
    """Delete every unused delivery code for a product. Returns count deleted.
    Used products_keys (used=1) are never touched — they're the audit trail
    for past orders, same reasoning as delete_product's cleanup."""
```

### New states / callback patterns

No new `ConversationHandler` states — the whole tab is plain
`CallbackQueryHandler`s (list render, row tap, select toggle, bulk confirm),
matching how `admin_products`/`manage_product` already work. Only the
existing `keys:<id>` and `edit:stock:<id>` conversation entry points are
reused for the two "act on one product" cases.

## Web: "Stock" tab inside `/products`

### Placement

`admin/src/app/(admin)/products/page.tsx` gets a small tab toggle
("All products" / "Stock") above the current grid — client-side state
(`useState`), no new route, no new sidebar entry. "All products" is today's
unchanged card grid.

### KPI strip

Three `KpiCard`s (reusing the existing component from the dashboard) via
`KpiRow`: **Out of stock** (count), **Low stock** (count, `effectiveStock <=
5` — same threshold `ProductCard` already uses; not unified with the bot's
`3` — see Open decisions below), **Total unused codes** (sum across keyed
products).

### Table

Existing `Table`/`TableRow`/`TableCell`/`Badge` components (same ones the
dashboard's "Recent activity" table uses), sorted the same way as the bot
list (out-of-stock → low-stock → rest, deactivated last). Columns:
checkbox, product name (with icon char if set), type pill (Manual/Keyed),
stock number (or ∞), status badge, and a row action link:
- Keyed → "Add codes →"
- Manual → "Edit stock →"

Both links open the **existing** `ProductFormDialog` (same component
`ProductCard`'s Edit button already opens) — no new dialog. This is the
same "reuse the existing single-product flow" call as the bot side.

### Bulk action bar

Appears above the table once ≥1 row is checked: **Deactivate** / **Reactivate**
/ **Clear codes** / **Clear selection**. Clicking a destructive one
(Deactivate, Clear codes) opens the existing `Dialog` confirm pattern
(`ProductCard`'s delete-confirm dialog is the template) before running.
Bulk apply = `Promise.all` over the existing per-product server actions
(`setProductActive`, and a new `clearProductKeys` action mirroring the
bot's `clear_unused_keys`) — client-side fan-out, no new bulk endpoint.

### New server action

`admin/src/app/(admin)/products/actions.ts`:

```ts
export async function clearProductKeys(id: number): Promise<{ deleted: number }> {
  // DELETE FROM product_keys WHERE product_id = id AND used = 0, return count
}
```

## Open decisions carried forward (not blocking)

- **Low-stock threshold mismatch**: bot uses 3, web already uses 5
  (`ProductCard`). This spec keeps each surface's existing number rather
  than unifying — flagging it here since an admin checking both screens for
  the same product may see different "Low stock" labeling. Fix separately
  if that mismatch turns out to matter in practice.

## Explicitly out of scope (skipped, not forgotten)

- **Viewing/browsing individual delivery codes** (not just the unused
  count) — no current screen does this either; add if a real need shows up.
- **Bulk "set stock number to X"** across several manual products — declined
  during brainstorming; the two bulk actions that shipped (deactivate,
  reactivate, clear codes) cover the requested "reduce stale accounts"
  workflow.
- **Restock/new-product announcement changes** — untouched; both new flows
  (bot "Add codes", web "Add codes") already trigger the existing restock
  announcement path (`keys_submit`'s `_offer_announcement` call / whatever
  the web equivalent is today) without modification.

## Testing

- Bot: `ALLOW_SELFCHECK_DB=1 python -m app.db.products` self-check gets a
  `_demo_clear_unused_keys` case (insert used+unused keys, clear, assert
  only unused rows are gone and the count matches).
- Manual verification pass (per this repo's `verify` skill/no-test-suite
  convention): walk both Stock tabs — browse, quick action on one keyed and
  one manual product, select mode with a mixed selection running each bulk
  action, confirm skipped-vs-applied counts are correct.
