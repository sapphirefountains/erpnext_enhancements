# Package Dispatch

An official, repeatable form for sending a package out — so shipping something is
no longer a messy, handwritten one-off.

Each **Package Dispatch** captures, in one place:

- **What's being sent, with a value per item.** Add a line per item; optionally
  pick a catalog **Item** to pull its name and selling value, or just type a
  one-off description and value by hand. The form totals a **Total Declared
  Value** — your reference for *how much to insure the package for* — and shows an
  "Insure for …" headline on the form.
- **A clean recipient address.** Structured fields (name, company, street, city,
  state, ZIP, phone) you type — no handwriting, and searchable later so you can
  always see where a package went. Optionally pick a **Customer** to auto-fill the
  address from their primary address.
- **A plain-English "what's inside" summary** to tell the store, auto-written from
  the item list if you don't type your own.
- **Delivery tracking** — store/carrier, tracking number, shipped/delivered dates,
  and a status (Not Shipped → Shipped → Delivered) derived from those dates. A
  **Mark Delivered** button on a submitted dispatch stamps the delivered date.

Submit to finalize it as an official record (it locks; amend to correct). Print
the **Package Dispatch Sheet** to hand over at the counter or keep on file.

## Files

- `doctype/package_dispatch/` — the main submittable form (`Package Dispatch`).
- `doctype/package_dispatch_item/` — the item child table (`Package Dispatch Item`).
- `api.py` — the catalog-item value + customer address auto-fill endpoints
  (gated by the switch).
- `setup_print_formats.py` — the Package Dispatch Sheet print format
  (`after_migrate`, idempotent).
- `workspace/shipping/` — the desk workspace (**Shipping**; shortcuts by status).
  Named distinctly from the doctype so its route (`shipping`) doesn't shadow the
  Package Dispatch list route (`package-dispatch`).

## The switch

The **Package Dispatch** master switch lives in **ERPNext Enhancements Settings →
Package Dispatch** and is **OFF by default**. It gates only the two auto-fill
conveniences (catalog-item value + customer address). The form, totals, print
sheet and submit flow all work regardless — with the switch off you simply type
the item values and address by hand. Flip it on to enable the auto-fill; no
deploy needed (the client reads the flag from `frappe.boot` on the next page
load, the server endpoints read the live value).

## Access

Permissions are granted to **System Manager** and a dedicated **Dispatch User**
role (seeded insert-only by `patches/seed_dispatch_user_role.py`). Assign the
**Dispatch User** role — directly or via a Role Profile — to everyone who should
be able to file a dispatch.
