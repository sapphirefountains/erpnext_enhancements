# `form_kit/` — the guided-entry engine

The server half of form kit P4 (ERPNext TASK-2026-02742): a DocType's form, walked one step at a
time, saved one step at a time onto the real record, with a running measure of how complete the
record is for its stage. The page that draws it is P4-B, the Desk page **Guided Entry**
(`enhancements_core/page/guided_entry`, `/desk/guided-entry/<DocType>/<name>/<step>`), whose pure
decisions are ES modules in `public/js/form_kit/`; the components it draws with are in
[`docs/app-design-system.md`](../../docs/app-design-system.md). This is a plain Python package, not
a Frappe module: no DocTypes, nothing in `modules.txt`.

Three decisions from the product owner (2026-10-13) shape it:

- **Config lives in code, reviewed by PR.** The Opportunity layout has been frozen since
  2026-06-09 and changes only by PR; how it is walked changes the same way. Nothing is editable
  from the Desk.
- **Keep showing everything.** Every field the form shows is in the flow. A config orders, groups,
  introduces and conditionally offers steps; it never hides a field the form shows.
  `tests/test_form_kit.py` compares the flow with the form field by field.
- **A new record is a real draft after step 1.** Step 1 asks only for what the record needs to
  exist, inserts it, and every later step is an ordinary save. Nothing typed lives only in a
  browser.

| File | Purpose |
|---|---|
| `steps.py` | `flow_steps(doctype, doc=None, user=None)`: the meta walked into steps, with each field's permission worked out for the user |
| `saver.py` | `create_record` (step 1 inserts the draft) and `save_step` (one step, optimistic lock on `modified`) |
| `completeness.py` | `stage_completeness(doctype, doc)`, pure; `completeness_for(doctype, names)`, the batch for the board |
| `api.py` | The five whitelisted endpoints, and the DocType allowlist in front of them |
| `field_help.py`, `help_text/*.json` | Field help (P4.3): each field's short `hint` and longer `help`, one file per DocType, changed by PR |
| `config/__init__.py` | `CONFIGS`, the registry and allowlist; the config schema and `check()` |
| `config/opportunity.py` | Opportunity: steps, intros, skipped widgets, step 1, stage lists |

## How the steps are built

`steps.sections_of(meta)` reads the form as the Desk lays it out: a Tab Break opens a tab, a
Section Break opens a section. Fields before the first break form the section `_top`; fields that
open a tab before any Section Break form a section keyed by the tab's fieldname. A section is
hidden when it or its tab is, exactly as `frappe/form/section.js` hides its wrapper.

Each configured step names the sections it holds (`sections`), may move single fields into
itself from wherever they sit (`fields`), and may `apply` only while a value is chosen. Merged
sections keep their own Section Break, with its `depends_on`, so a condition such as "the Lost
section shows only when the status is Lost" still holds on the page. Empty columns and empty
sub-sections are tidied away. **A section no step claims becomes a step of its own**, titled with
its label, placed before the `final` step, so a section added to the DocType later shows up with no
code change. A field is left out only if the form hides it, or if the config's `skip_fields` lists
it with a reason (HTML fields whose content the form's JavaScript draws). `show_hidden` is the one
exception the other way: a field the form hides but sets through a widget the flow cannot draw.

**Step 1 makes the record.** Its fields are flagged `"create": 1`: the config's `create.fields`,
plus any required field without a default that the meta gains later (moved into step 1, so a new
required field can never make the insert impossible). `create.filled_on_create` names a required
field the new record fills itself, with how.

## Permissions

These endpoints are reachable by any signed-in user, so every one checks the DocType allowlist
first (`CONFIGS`; anything else is refused before a document is loaded) and then the caller's
permission on the DocType or record. Per field, the rule is the Desk form's own
(`frappe/public/js/frappe/model/perm.js`, `get_field_display_status`, and
`Document.apply_fieldlevel_read_permissions`, at `version-16`):

| The user can… | The field is… |
|---|---|
| write the document **and** write the field's permlevel | editable (unless `read_only`, `set_only_once` on a saved record, or in a read-only step) |
| read the field's permlevel | sent, read-only |
| not read the field's permlevel | not sent at all, neither its definition nor its value |

Level 0 follows the document permission (`frappe.has_permission(doctype, "write", doc)`, which
includes user permissions, sharing and if-owner rules); a higher level follows the role rows
(`Meta.get_permlevel_access(...)`). A child table's columns answer to the parent's rows. A
submitted or cancelled document is read-only throughout.

**The saver never trusts the client's idea of what is editable.** It recomputes the steps for the
user and assigns only fields offered as editable, one by one, never `doc.update(values)`. A crafted
key (`owner`, `docstatus`, a read-only stamp, a level the user cannot write) is ignored, the same
as a field the page did not send. Frappe's own `validate_higher_perm_levels` still runs inside
`save()` as a second fence.

## Payloads

`get_flow(doctype, name=None)`, GET:

```json
{
  "doctype": "Opportunity",
  "name": "CRM-OPP-2026-00123",
  "modified": "2026-10-13 10:00:01.000000",
  "steps": [
    {
      "key": "scope_design", "title": "Design scope", "intro": "…", "tab": "Scope",
      "applies": {"field": "custom_value_stream", "row_field": "value_stream", "in": ["Design"]},
      "active": true, "read_only": 0, "final": 0, "link": null, "create": 0,
      "fields": [
        {"fieldname": "custom_design_customer_requests", "fieldtype": "Table", "label": "…",
         "options": "Design Customer Requests", "hint": "…", "help": null, "read_only": 0,
         "child_fields": [{"fieldname": "design_customer_requests", "fieldtype": "Long Text",
                           "in_list_view": 1, "read_only": 0, "hint": "", "help": null}]},
        {"fieldtype": "Column Break", "fieldname": "custom_column_break_kb4nu"},
        {"…": "…"}
      ]
    }
  ],
  "values": {"custom_design_customer_requests": [{"name": "a1b2c3", "idx": 1, "design_customer_requests": "A koi pond"}]},
  "completeness": {"stage": "Needs Analysis", "done": 7, "total": 9, "missing": [{"fieldname": "…", "label": "…"}]},
  "can_write": 1
}
```

With no `name` it describes a new record: `name` and `modified` are null, `values` are the new
record's defaults (`frappe.new_doc`), and step 1 (`"create": 1`) is what `create_record` takes. It
also carries `prefill_fields`: what `create_record`'s `prefill` may set (see below).
`applies` is data the page re-evaluates as the user picks values; `active` is its value for the
record as loaded. A step that does not apply is still sent (the page can show it greyed, or skip it).
`hint` and `help` come from the DocType's help file (see *Field help* below); a field the file is
silent about falls back to its docfield description, with `help` null. `help_article` is the
Knowledge Article each field's *Learn more* opens, at its step's anchor, or null.

`create_record(doctype, values)` and `save_step(doctype, name, values, modified, step=None)`, POST,
`values` a JSON object of `{fieldname: value}`. A table's value is its full list of rows: a row with
the `name` of a stored row updates it, a row without one is added, a stored row left out is
removed. A table not sent is untouched. With `step`, only that step's fields are saved. Success:

```json
{"name": "CRM-OPP-2026-00123", "modified": "2026-10-13 10:00:02.000000",
 "values": {"website": "https://smithponds.com"}, "completeness": {"…": "…"}}
```

`create_record` also takes an optional `prefill`, a JSON object of what the record was started with
somewhere else (`frappe.new_doc`'s options, or a mapped document such as ERPNext's *Create >
Opportunity* on a Customer, which copies the party, a contact, an address and the value streams).
The server takes from it only `steps.prefill_fields`: every field the flow lets this user write, in
any step, plus the config's `create.prefill_fields` (hidden fields such a start fills; on
Opportunity `contact_person`, `custom_description`, `company`), each still held to its permlevel.
Step 1's own fields are never taken from it: step 1 is what the person saw and sent.

`values` are read back after the save, so they are what the hooks stored (the `"*"` normalize
hook's phones and websites, for one); the page replaces what it shows with them, and keeps
`modified` for the next save.

**The conflict shape.** When the record's `modified` is no longer the one sent, nothing is saved
and the call returns, not raises:

```json
{"conflict": true, "name": "CRM-OPP-2026-00123", "modified": "2026-10-13 10:05:00.000000",
 "modified_by": "office@sapphirefountains.com",
 "server": {"custom_opportunity_name": "Smith Residence - Pond", "…": "…"}}
```

`server` is the step's fields as they now stand (the fields sent, when no `step` was named),
serialised like `values`, so the page can show both versions and let the user choose; saving the
choice again with the new `modified` goes through. The offline queue (P4.7) depends on this
shape: a kept save replayed against a record someone else changed comes back as a conflict, keeps
the local copy and asks, rather than overwriting. `save_step` without a `modified` is refused,
because a save that cannot be checked could overwrite someone silently.

**Errors** are the Desk's own. A failed validation (`frappe.ValidationError`: on Opportunity, Closed
Won without the three ranks, Lost without a lost reason) or a missing permission
(`frappe.PermissionError`) is raised from `insert()`/`save()` exactly as the Contract Builder's
`save_builder` and the Visit Wizard's `save_visit` raise it: HTTP 417/403 with the message in
`_server_messages`, which `frappe.call` shows. Nothing is half-saved. A conflict is a return value
because it is an expected outcome the page handles, not an error.

`completeness_for(doctype, names)`: `names` a JSON list of up to 500 names; returns
`{name: completeness}` for those the caller can read (one `frappe.get_list`, so an unreadable
record is simply absent) with one bound query per child table, never one per record.

`my_records(doctype)`, GET: the guided page's start screen. `{"label", "rows": [{"name", "title",
"details", "modified"}], "limit"}`, the caller's open records newest change first, through
`frappe.get_list`. The config's `mine` says whose (`owner_field`), which are open (`open.not_in`),
which to leave out (`hide_flag`: on Opportunity, the historical deals) and what a row shows.

`declare_lost(doctype, name, reasons, competitors=None, detail=None, modified=None)`, POST: the
config's `lost` (on Opportunity, ERPNext's `declare_enquiry_lost`), with `reasons` and
`competitors` as JSON lists of names or rows. It is locked on `modified` like `save_step` and
returns the same result or conflict shape. `get_flow` sends `lost` (the status that opens the
page's Set as Lost dialog, and the two child DocTypes), `distinct` (fields the page warns should
not share a value) and `clears` (a field whose change empties others).

## Where a record opens

Nik, 2026-10-13: "I want these guided setups to be the default for new and existing documents of
the DocTypes we are making guided setups for." So for anyone who may open the guided page, a
configured DocType's records open there; the form is the fallback. `public/js/guided_default.js`
(global, in `erpnext_enhancements.bundle.js`) does it with v16's own hooks:

- **New**: `frappe.create_routes[doctype]`, which `frappe.new_doc` (the list's + Add, the awesomebar,
  the Kanban's add card, `customer.js` and `lead.js`) routes to instead of a form; its options
  arrive as route options, step 1's starting values.
- **Existing**: a `before_show` on the Form view factory (the List factory re-routes the same
  way), replacing the form's history entry so Back still leaves the way the person came.
- **The form stays** for a record opened with the guided page's *Open form* (remembered for the
  tab in sessionStorage until the form's *Guided* button), a link into one tab of the form (a
  `#hash`), a record just saved from the full form, and an unsaved record the Desk holds that the
  guided page cannot take whole. One it can take (the Customer mapping) opens on step 1 with its
  values. The rules are `public/js/form_kit/opening.js`, run under node by `test_guided_entry`.
- `frappe.boot.sf_guided` lists the DocTypes, with what a new one accepts. site_config
  `disable_guided_default` empties it, and every record opens in the form again.

## Completeness

`config["stages"]` lists, per stage (the value of `stages.field`, `status` on Opportunity), what a
record at that stage should have. A requirement is `{"field"}` (a table needs a row with its
`row_field` filled; `"positive"` asks a number above zero), `{"any": [...]}` (one of several, such as
`primary_contact` or `contact_person`), or `{"chosen_rows"}` (for each choice made in a table such as
the value streams, its own table has a filled row; choices without a table do not count). Every
requirement carries the label the page shows when it is missing. A stage with no list is measured
against `stages.default`.

Opportunity (decided from prod fill rates, TASK-2026-02773; cumulative):

| Stage | Asks for |
|---|---|
| Qualification, On Hold | account, Opportunity Name, a Value Stream, Opportunity Owner, Opportunity Summary, a contact (`primary_contact` or `contact_person`) |
| Needs Analysis | + Lead Source, Expected Closing Date, Customer Requests for a chosen stream (Design, Build, Service, Events; Products and Delivery have no requests table) |
| Proposal/Price Quote, Negotiation/Review | + Opportunity Amount above zero, a site address (`primary_address` only: `customer_address` is the Billing Address since v1.633.0) |
| Closed Won | + Scope, Schedule and Budget Rank |
| Lost | the Qualification list + a lost reason (ERPNext's `lost_reasons` table; the app's `custom_lost_reason` was removed in v1.159.0) |

Rarely filled fields (scope description, requested dates, budget notes, the Events schedule,
budgets, the initial invoice, contributors, deliverables) are deliberately not counted.

## Field help

Nik, 2026-10-13: every field gets a short hint, always visible under it, and a longer explanation
behind an (i); the text lives in the repository and changes by PR, never from the Desk.
`help_text/<doctype>.json` holds `{"article", "fields": {fieldname: {"hint", "help"}}}`.

- **The hint is also the field's description** (the custom field in the fixtures, or a
  `description` Property Setter for an ERPNext field), so the Desk form, Quick Entry and grids show
  the same line as the flow. `test_form_kit` fails when the two drift, and when the file does not
  cover exactly the fields the flow shows.
- **The flow** sends `hint` and `help` per field (`steps.field_def`).
- **The standard Desk form**: `field_description_icons.js` already shows a description as an ⓘ
  tooltip. For a DocType with a help file (`frappe.boot.sf_field_help`) the tooltip adds the longer
  explanation and a *Learn more* link, fetched once per DocType from `get_field_help` with a plain
  `fetch` (frappe.call shows its own dialogs on errors; a tooltip must not).
- `get_field_help(doctype)`, GET: `{"article", "fields": {fieldname: {"help", "anchor"}}}`, only
  fields with an explanation that the caller may read; `anchor` is the field's step key.

## Adding a DocType

1. Write `config/<doctype>.py` with a `CONFIG` dict (the schema is in `config/__init__.py`'s
   docstring) and add it to `CONFIGS`. That line is the allowlist entry: review it as one.
2. List every HTML field without static content in `skip_fields`, with what draws it.
3. Give every step a title and a one-sentence intro, in American English and the voice of the
   DocType's field guide.
4. Extend `tests/test_form_kit.py` with a meta built from the real JSON, fixtures and Property
   Setters the way it builds Opportunity's, so the schema checks (every fieldname exists, every
   section exists, every step key is unique) and "every field the form shows is in exactly one step"
   run against it.
