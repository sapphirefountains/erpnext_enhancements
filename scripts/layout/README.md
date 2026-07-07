# Form layout generator

Regenerates the `<DocType>-main-field_order` Property Setter values in
`erpnext_enhancements/fixtures/property_setter.json` from declarative layout
specs, and upserts the layout-related label/collapsible setters each spec
declares. Hand-editing 200-element field_order arrays is unreviewable; this
keeps the design intent in small, diffable spec files and makes the arrays
reproducible.

**Re-run this after every ERPNext upgrade** (new standard fields otherwise get
fallback placement — cosmetic, but the arrays go stale):

```
python scripts/layout/generate_field_order.py            # rewrite fixtures
python scripts/layout/generate_field_order.py --check    # lint only, write nothing
python scripts/layout/generate_field_order.py --only "Purchase Order"
python scripts/layout/generate_field_order.py --bench-root /path/to/frappe-bench
```

Default `--bench-root` is the WSL dev bench (`\\wsl$\Ubuntu-26.04\home\nbbsh\frappe-bench`);
the script only reads doctype JSONs from it.

## Spec files (`specs/*.json`)

```jsonc
{
  "doctype": "Purchase Order",
  // reqd fields allowed to live after the first Tab Break (lint 4 whitelist)
  "reqd_after_first_tab_ok": ["status"],
  "layout": [
    "naming_series",                          // explicit field placement
    {"section": "currency_and_price_list"},   // this Section Break + its bench-default
                                              // members (in bench order), minus fields
                                              // placed explicitly or claimed by a
                                              // narrower macro
    {"tab": "terms_tab"}                      // same, up to the next Tab Break
  ],
  // label/collapsible/etc. Property Setters that are part of the layout design
  "setters": [
    {"field": "supplier_invoice_details", "property": "collapsible",
     "value": "0", "property_type": "Check"}
  ]
}
```

Rules:

- Custom fields (from `fixtures/custom_field.json` or `code_owned_fields.json`)
  are never pulled in by macros — every custom field must be placed explicitly.
- A `{"section": ...}` macro inside another macro's tab range wins the overlap
  (fields go to the narrowest range); explicit placement always wins.
- `code_owned_fields.json` registers custom fields that live in app code
  (`is_system_generated = 1`, e.g. the Item pump fields) or are framework-owned
  (`Purchase Invoice.workflow_state`) — they exist in live meta but not in the
  fixtures, and the arrays must still cover them.

## Hard-fail lints

1. Each generated array is an exact permutation of
   (bench standard fields ∪ fixture custom fields ∪ registry fields).
2. No Column Break before the first Section Break of a tab segment unless a
   leaf field precedes it (and never directly after a Tab Break).
3. Every visible Tab Break contains at least one visible leaf field.
4. Every `reqd` field is visible and placed before the first Tab Break,
   unless whitelisted in `reqd_after_first_tab_ok`.

The dev-bench complement to lint 1 (run after `bench migrate`): assert
`set(field_order) == {f.fieldname for f in frappe.get_meta(dt).fields}` and that
meta order equals the array — this proves verbatim ordering AND catches custom
fields from other installed apps that the repo doesn't know about.
