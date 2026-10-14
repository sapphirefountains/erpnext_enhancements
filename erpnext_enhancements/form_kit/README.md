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
| `api.py` | The whitelisted endpoints, and the DocType allowlist in front of them |
| `party.py` | The Party entry component's one read (P3.2): an account's default contact and addresses, and which of the record's are the account's. See *The Party entry component* below |
| `field_help.py`, `help_text/*.json` | Field help (P4.3): each field's short `hint` and longer `help`, one file per DocType, changed by PR |
| `config/__init__.py` | `CONFIGS`, the registry and allowlist; the config schema and `check()` |
| `config/opportunity.py` | Opportunity: steps, intros, skipped widgets, step 1, stage lists |
| `config/item.py` | Item (P8.1): the core steps with their blocks, the `more` steps, `open_by_default: False` |
| `blocks.py` | The information blocks (P3.1): Party, Site, Schedule window, Scope by pillar, Money, Crew, with their fields per DocType and their three faces. JavaScript twin: `public/js/info_blocks/blocks.js`. See *Information blocks* below |

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
`modified` for the next save. Since v1.677.0 a `save_step` without `step` also returns every field
the flow shows that **the save itself** changed besides the ones sent (ERPNext's
`Opportunity.map_fields` filling an empty contact from a new account, `party_mirrors` copying the
hidden contact up): before, the page kept showing the old value, and its next save put it back.

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

`completeness_for(doctype, names)` (the Opportunity board's meters, P6.1, call the Python function directly, in batches of 500): `names` a JSON list of up to 500 names; returns
`{name: completeness}` for those the caller can read (one `frappe.get_list`, so an unreadable
record is simply absent) with one bound query per child table, never one per record.

`my_records(doctype)`, GET: the guided page's start screen. `{"label", "rows": [{"name", "title",
"details", "modified"}], "limit"}`, the caller's open records newest change first, through
`frappe.get_list`. The config's `mine` says whose (`owner_field`), which are open (`open.not_in`),
which to leave out (`hide_flag`: on Opportunity, the historical deals) and what a row shows.

`get_flow` also sends `won` (`{"status", "status_field", "fields"}`: what a move into Closed Won must
carry, the config's `won`; the side panel asks for these fields before it saves the move), `closed`
(`{"field", "in"}`: the statuses that close a record, from the config's `mine.open`) and, for a
new record only, `quick` (`{"fields", "fixed", "owner_field", "stage_field"}`: the board's quick
add, the config's `quick`). A `quick.when` rule that raises never takes step 1 down: the field is
**offered** (read as "not required", Lead Source would be hidden while `enforce_source` may still
refuse the insert without it), and the failure is logged once per process, not on every page open.

`party_details(doctype, party_type, party, contacts=None, addresses=None)`, GET: the Party entry
component's question when the account changes. See *The Party entry component (P3.2)* below.

`declare_lost(doctype, name, reasons, competitors=None, detail=None, modified=None)`, POST: the
config's `lost` (on Opportunity, ERPNext's `declare_enquiry_lost`), with `reasons` and
`competitors` as JSON lists of names or rows. It is locked on `modified` like `save_step` and
returns the same result or conflict shape. `get_flow` sends `lost` (the status that opens the
page's Set as Lost dialog, and the two child DocTypes), `distinct` (fields the page warns should
not share a value) and `clears` (a field whose change empties others).

## One editor, two shells (P4.4)

The page that walks a record and the side panel that edits one from a list or board run **the same
record editor**, `public/js/form_kit/ui/editor.js` (`RecordEditor`, v1.675.0): one step's fields in
a `frappe.ui.FieldGroup`, the values as shown and as saved, the save queue and its conflict chooser,
the phone's journal, step 1's create and Set as Lost. It draws with frappe and jQuery, so it is
not among the pure modules in `public/js/form_kit/*.js` (those are what the node suites import);
`guided_entry.bundle.js` carries it.

- **The guided page** keeps what only it has: its routes, the band, rail, summary and footer, the
  start screen. Its class is defined before the bundle loads, so the editor's methods are mixed onto
  it when the bundle arrives (`mix_editor`), never over a method the page defines.
- **The side panel**, `public/js/form_kit/ui/side_panel.js` (`class SidePanel extends
  RecordEditor`), lives in the planner kit's drawer, full screen on a phone. The page that opens it
  owns the address (the drawer is opened with `history: false`): each panel is a route, Back closes
  it, reload reopens it, and a step change *replaces* the address. Three kinds:
  - **edit**: a record, one step at a time, autosaved as on the guided page; closing it offers an
    Undo toast that puts back what this panel changed, locked on `modified`, and then sends
    `sf-record-changed` so an open guided page reloads. **No Undo** when the edit moved the record
    into or out of a closing status (`get_flow`'s `closed`, plus `won` and `lost`; `panel.js`
    `undo_allowed`): a plain save back out of Closed Won would skip `revert_won_status`'s refusal
    once a project exists and leave the won date set. It fails closed (v1.677.0): no flow, or one
    that sends neither `closed` nor `won`, offers no Undo.
  - **move**: what a move into a stage must carry (`won`'s fields, or `lost`'s reasons), then the
    move, in one save. The queue is `manual` (`save_queue.js`: nothing autosaves) and the journal
    keeps nothing (`hold_saves`), so nothing reaches the record until the person commits.
  - **new**: the config's `quick`, created through `create_record` with the create's `client_id`.
    `quick.when` adds a field while the server says a new record needs it (on Opportunity the Lead
    Source, while `crm_enhancements.attribution.lead_source_required`); `get_flow` decides, so the
    Desk never reads the Settings Single.
- **Known limitation: one journal union per editor.** The phone's entry for a record is written by
  whichever editor last saved it, from that editor's own queues (`write_journal`). The guided page
  and a side panel open on the same record in one tab (or two tabs) each hold their own queues, so
  one can rewrite the entry without the other's unsent values. A shared, module-level registry of
  live queues per user and record would close it, but the bases and held choices would have to be
  shared too; left for a later change. In practice the two are rarely open on one record at once,
  and a lost entry only matters when the signal drops at that moment.
- What a shell must provide (`showing`, `load`, `refresh_chrome`, `after_create`, `show_created`,
  `still_new`) is listed at the head of `editor.js`. The panel's pure decisions are
  `public/js/form_kit/panel.js` (`gate_for`, `field_defs`, `quick_start`, `create_extras`,
  `undo_values`), run under node by `tests/test_opportunity_board_panel.py`.

Step 1's create carries, as `prefill`, whatever else step 1 shows that the person changed (the
summary, a close date): `create_record` assigns only the "create" fields, and before v1.675.0 the
rest was silently dropped.

## Notes and comments, on every guided DocType (v1.678.2)

**Rule (Nik, 2026-10-14): "We need the comments visible for every doctype we are making the views
for now and in the future."** A guided view replaces the Desk form, and with it the form's timeline,
so the record's history has to come with it. Both shells draw an empty `.ge-comments` section under
the steps, the guided page always and the side panel on every edit, and the shared editor fills it
(`editor.js mount_comments`) for any saved record of any DocType:

- **Notes**: every notes store the DocType has, through `record_notes` (GET; the registry check and
  read on the record; plain text only, written with `.text()`). Notes and Comments are separate
  stores and both show (Nik, 2026-10-14: "just include both where appropriate"). The stores
  (`_note_sources`, `NOTE_TABLES`):
  - a table of **CRM Note**, ERPNext's Notes tab (an Opportunity has 1,049 notes on 235 deals on
    prod; Lead and Prospect have the table too);
  - a table of **Project Notes**, Project's imported history (`custom_comments_table`, 283 rows,
    hidden on the Desk form);
  - a **`notes` text field** (Project has one), listed first as one note labelled "Notes field",
    with no author or date.

  Table rows are merged newest first. A store at a permlevel the user cannot read is left out. Which
  stores a DocType has is decided by its meta, never by its name. Adding one runs the DocType's own
  `add_note` through `run_doc_method` (only CRM Note DocTypes have it; elsewhere a new note is a
  Comment),
  after what is queued has saved, then draws only the Notes list again (no page reload). The
  record's `modified` moves, but the notes table is in no step, so the next step save's conflict
  check settles it without asking (`save_queue` `split_conflict`).
- **Comments**: the Desk's own Comments App (`public/js/comments.js`), mounted with a stand-in
  `frm` that carries only `doc.doctype`, `doc.name` and the wrapper, which is all it reads. It reads
  and writes the native Comment rows the form timeline shows, so a comment is the same on both.

A new DocType gets both with no work. `tests/test_guided_entry.py TestTheCommentsAreShown` fails if
the editor singles a DocType out, or if the Comments App starts reading more of `frm`.

## View settings (v1.684.0)

**Nik, 2026-10-14:** "once a document has been created via the flow, the presentation could be
better ... Let's make it somewhat flexible and customizable by the user and their preferences", with
"all sections on one page, but leave the ability for tabs to be used and visible" as the default for
a saved record, and "Preferences should be saved to their instance and not change it for everyone
else's views." A **View** button (a gear) in a saved record's band opens the settings, applied as
they change; a person's first saved record shows a short pop-up about them, once.

**A layout is a presentation of the flow, never a different form.** Every layout draws the same
steps with the same fields and the same controls through the editor's one renderer
(`render_fields`), with the same save queue, conflicts, phone journal, completeness, help, Party
component and notes and comments. One page and Tabs draw one FieldGroup per step, held together by
`public/js/form_kit/field_groups.js` (`FieldGroups`: one `fields_dict` for them all, and every
step's `refresh_dependency`), so `collect_step`, `collect_tables`, `field_changed`, `show_value`,
the conflict chooser and `focus_field` work across all of them; `shown_steps()` is "the steps on
screen" and `current_step()` stays "the step in view" (scroll-spy, or the section being typed in).

| Setting | Values (first is the default) |
|---|---|
| Layout | One page (every step a section, under its Desk tab; the tab bar a sticky jump menu with scroll-spy; sections collapse) · Tabs (one tab at a time; a tab is a route) · Step by step (the setup flow's page) |
| Where the steps sit | left · right · top (always top on a phone) |
| Summary | right · left · hidden (folds above the form on a phone) |
| Notes and comments | below the form · side column · collapsed behind a "Notes and comments (N)" bar |
| Record tools (`.ge-tools`) | side column · below the form · collapsed |
| Focus mode | off · on (only the form and its nav; notes and tools behind their bars) |
| Text size, contrast, spacing | normal/large/larger · normal/high · comfortable/compact (CSS tokens only) |
| Field hints | under each field · only in the (i) (which then holds the hint) |
| Fields | all · only fields with a value (never hides a required field, or one the stage still needs) |

Besides those: **pinned sections** (drawn first in One page and Tabs, marked in the nav); **where a
record opens** (where the person left it, the first section, or a chosen one); **fields and sections
put away** per person (never a required field that is empty, never one in `completeness.missing`;
the bar over the form counts them, with "Show hidden"); **summary fields** (up to 12 of the record's
fields instead of the DocType's summary); **saved views** (named presets of the settings) and
**Reset to defaults**; and **shortcuts**, none of which fire while typing except Save: J/K next and
previous section, Ctrl/Cmd+S save now, C notes and comments, V view settings, F focus mode, ? the list.

- **Routes.** Step by step and Tabs are routes (a step, or a tab's first step), so Back and Forward
  walk them. One page is one screen, the record's own address; scrolling is not a history entry.
  Reload opens where the person left off (`remember_guided_section`, sent as a beacon on leaving).
- **A new record's setup flow is always step by step** (`view.js SETUP_VIEW`), and so is the rest of
  the walk its step 1 starts, until the person leaves the record or changes a setting.
- **The store.** The person's own `__UserSettings` row under `Guided Entry`, key `guided_view`, through
  `api/planner_views_prefs.py`, written straight to the table (never frappe's Redis-only user
  settings, which the deploy flushes): `get_guided_view()` GET; `save_guided_view(doctype, data)`
  POST, replacing only the parts sent; `remember_guided_section(doctype, name, step)` POST, the last
  50 records; `mark_guided_intro_seen()` POST. A DocType outside `CONFIGS` is refused and read on it
  is checked. Everything is cleaned on the way in and out (`GUIDED_SCHEMA`, `GUIDED_MAX`; the page's
  twin is `view.js`, held equal by `tests/test_guided_view_settings.py`).
- **The board's cards** (`opportunity_board`): a "Card fields" picker in the filter bar, saved with
  the board's own views as the text key `card` (`board.js card_parts`). A view that never chose shows
  the card as it always was.
- **The side panel stays step by step** with no settings: it is narrow, and it shares only the
  tightened field styles.
- **A DocType with step blocks stays step by step** (form kit P8.1, the Item flow): its blocks hold one
  step's state at a time and its flow ends at Done and More details, which only Step by step has
  (`view.js effective_view` `steps_only`, from `has_extension`). Every other setting applies.
- **Field styles, every layout and the side panel.** The (i) stays on its label's line; a form's
  columns wrap at about 240px (four, two, one); inputs at the Desk's height with an edge in both
  themes, 44px on a phone; text boxes start at three lines and grow to 320px (`fit_text`); hints
  clamp to two lines (a longer one is whole in the (i)); the Datetime time-zone line only when the
  user's zone is not the site's (`df.hide_timezone`); the fields' left edge on the step title's.

## No signal (P4.7)

The guided page keeps working when the signal drops while it is open. What has not reached the
server is kept on the phone, in localStorage:

- one entry per person and record (`ge-unsent:<user>:<DocType>/<name>`): the unsent values of every
  queue still holding the record, each field's server value from before the change, and `modified`;
- one entry per record not created yet (`ge-new:<user>:<DocType>:<client_id>`): step 1 as typed.

Keys carry the user, so on a shared phone nobody sees or saves another person's copy. Entries past
30 days are swept when the page loads. The rules are `public/js/form_kit/journal.js`, run under
node by `test_guided_entry`:

- **Opening the record again** puts kept values back by the server's current value: still the old
  one, the kept value is saved; already the kept one, it is dropped; anything else, the person
  chooses (the conflict chooser), and the phone keeps their value until they have. A kept change to
  a field they can no longer edit is reported, not silently dropped.
- **A failed save** that is the network's doing (`try_later`: no answer, 408, 425, 429, 502–504,
  or a 5xx without an exception type) waits for the signal: the browser's `online` event, and every
  20 s.
- **`create_record(..., client_id)`** stores the id on the record (the config's
  `create.client_id_field`; on Opportunity the hidden, unique, no-copy `custom_guided_client_id`)
  and answers a resend from the same user with that record, marked `deduped` (the page then saves
  whatever step 1 holds that differs). In the database, not the cache: the deploy flushes the cache
  several times a day. The unique index settles a resend racing the first insert: it waits,
  collides, and a locking read returns the winner. `find_created(doctype, client_id)`, GET, lets a
  page that put back a kept start ask whether that create arrived after all.

Opening ERPNext from cold with no connection is a separate problem: see
`docs/offline-cold-start-spike.md` (P4.8).

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
- `frappe.boot.sf_guided` lists the DocTypes, with what a new one accepts and whether its records
  open on the guided page: `default` for a saved record, only for someone with write permission on
  the DocType, and `default_new` for a New, only with create permission. A person who may only read
  keeps the form. site_config `disable_guided_default` empties it, and every record opens in the
  form again.
- **Not every flow is the default** (P8.1). A config with `open_by_default: False` (Item) keeps
  the form as where its records open: `default` is 0, `guided_default.js` sets no create route and
  redirects nothing (`opening.js` `opens_by_default`), and the flow is reached on purpose, by the
  form's *Guided* button (`sf_guided.has_flow`), the guided page's start screen (listed only for
  people who can create one) and `/desk/guided-entry/<DocType>/new`. Absent, the key is True, so
  Opportunity is unchanged.

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

## Information blocks

P3 (TASK-2026-02753): the same piece of information is entered, shown in a report and printed the
same way. `blocks.py` is the one definition (P3.1, TASK-2026-02754, v1.669.0). For each block it
names the fields that carry it on each DocType, and its three faces:

- **entry**: the kit component that enters it, as `{"module", "export"}` (an ES module under
  `public/`), or `{"pending": "<task>", "builds": "<what>"}` when the component is not built yet;
- **report**: the report face's column kinds (`public/js/report_face/cells.js` `KINDS`);
- **print**: the helpers a print format calls (`print_style` / `print_lookup`, registered as Jinja
  globals), Frappe's own `frappe.format` (listed in `FRAMEWORK_HELPERS` with the reason), or pending.

| Block | Opportunity | Project | Entry | Report | Print |
|---|---|---|---|---|---|
| Party | `opportunity_from`, `party_name`, `primary_contact`, `customer_address`; mirror `primary_contact` → `contact_person` | `customer`, `primary_contact`, `custom_billing_address` | `form_kit/ui/party.js` `PartyEntry` (P3.2) | `party` | `ps_party` |
| Site | `primary_address` | `primary_address` | `form_kit/ui/party.js` `PartyEntry` (P3.2) | `party` | `ps_address` |
| Schedule window | `utils/date_pairs.PAIRS["Opportunity"]` | `PAIRS["Project"]` + ERPNext's `expected_*` and `actual_*` pairs | pending TASK-2026-02753 | `date`, `datetime` | `ps_date`, `ps_datetime` |
| Scope by pillar | `custom_value_stream` + each stream's Customer Requests and Deliverables table | the same | `form_kit/fields.js` `text_list_column` | `pillar` | `print_style.STREAM_PILLAR`, `pillar` |
| Money | `opportunity_amount`, `custom_materials_budget`, in `currency` | `custom_project_dollar_amount`, `estimated_costing`, `custom_materials_budget`, `custom_bid_cost`, company currency | pending TASK-2026-02753 | `money` | `frappe.format` |
| Crew | `opportunity_owner`, `custom_scope_contributors` | `custom_project_owner`, `custom_technical_lead`, `custom_scope_contributors` | pending TASK-2026-02753 | `party` | pending TASK-2026-02753 |

Decisions:

- **A face is never left out; it may be pending.** A component that does not exist yet is declared
  with the task that builds it and a sentence saying what it is. `check()` refuses a block with no
  face declared at all, a pending face without a task, and an unknown report kind;
  `tests/test_form_kit_blocks.py` also fails when a named module does not export what it names, a
  print helper is not defined (or a `ps_` helper not registered in `hooks.py`'s Jinja globals), or a
  block names a field the DocType's meta does not have.
- **Point at the rules, don't copy them.** The Schedule window is `utils/date_pairs.PAIRS` (P2.2) plus
  the pairs ERPNext validates itself, which `date_pairs` deliberately leaves out. Scope by pillar is
  built from `config/opportunity.REQUEST_TABLES` and each stream's pillar is `print_style.STREAM_PILLAR`.
  Money has no app print helper because Frappe's currency formatting is already the one answer.
- **Party's address is the Billing Address; Site is the job site.** The plan wrote Party as
  `party_name` + `primary_contact` + `primary_address`, before v1.633.0 made `customer_address` the
  visible *Billing Address* and kept Primary Address as the job site (Nik, 2026-10-13). So Party
  carries the account, the contact and where invoices go, and Site carries where the work is. The
  P3.2 component picks both.
- **Mirrors.** `mirrors` names a hidden stock field kept equal to a visible one, read by
  `crm_enhancements/party_mirrors.py` on save. Opportunity's `primary_contact` → `contact_person` is
  the only one: Project has no stock contact field. Project's equivalent pair for P7 is the address:
  `primary_address` beside the older `custom_customer__lead_address` link, which `api/pickup_routing.py`
  and `api/travel.py` still read as a fallback; it is left alone until P7 makes Project guided.
- **The meta in the test is the v16 one.** ERPNext's DocType JSON at `origin/version-16`, trimmed
  into `tests/fixtures/form_kit/erpnext_v16_opportunity.json` and `erpnext_v16_project.json` (each
  `_source` says how), plus this app's Custom Fields and the fields `setup/custom_fields.py` adds on
  migrate. The sibling `erpnext` checkout's working tree is `develop` (v17), the wrong tree.
- **The JS twin** (`public/js/info_blocks/blocks.js`) is `resolved()` written out, held equal under
  node. Change the Python first.
- **Roles** (v1.677.0). The Party and Site blocks say what each field *is* to the entry component:
  `roles` (`account_type`, `account`, `contact`, `address`) and `accounts`, the account kinds the
  record may name. `check()` refuses a role that names a field the block does not carry. The
  component reads them through `blocks.party_roles(doctype)` (JS twin: `form_kit/party.js`
  `party_roles`), never fieldnames of its own, so Project (P7) gets it by adding its rows here.

## The Party entry component (P3.2)

TASK-2026-02755, v1.677.0. One component, `public/js/form_kit/ui/party.js` (`PartyEntry`), made by
the record editor (`editor.js` `party_entry()`), so the guided page and the side panel get the same
thing. Its decisions are `public/js/form_kit/party.js`, pure, run under node by
`tests/test_form_kit_party.py`. On Opportunity:

| Field | Search | "+ New" |
|---|---|---|
| Account (`party_name`, kind `opportunity_from`) | Frappe's own | Customer: the app's quick entry (`party_quick_entry.js`), then picked. Lead or Prospect: its form in a new tab, with a toast saying to pick it here once saved (neither has a quick entry on this site, and leaving the page would lose the step) |
| Primary Contact | the account's own (`fields.js` `link_query`, ERPNext's `contact_query`) | the app's Contact quick entry, linked to the account and the deal, then picked |
| Billing Address (`customer_address`) | the account's own (`address_query`) | the app's Address quick entry, starting on "Also <account>'s address" |
| Primary Address (the site) | the account's own | the same dialog, **no default** for "Whose address is this?" (the 2026-10-06 rule: a job site may be a general contractor's), type Shipping |

Decisions, for review:

- **The dialogs are the Desk's own, not a copy.** `contact_address_quick_entry.js` gained
  `contacts_ux.create_for(doctype, record, opts)`: the guided route is not a Form, so the dialog is
  handed the record's context (`PARTY_CONTEXT`, the same table the Opportunity form uses) instead of
  resolving one from the route. Everything else is unchanged: the Account field, links[0] naming
  the record, the custom_* phone and email fields, Google Places, the coordinates. Frappe's own
  "Create a new Contact" in a Link's dropdown is routed through it too (the control's `new_doc`), so
  nothing created from these pages is ever unlinked. A refused insert keeps the dialog open
  (`skip_redirect_on_error`) instead of opening the full form over the page.
- **Phones and emails** typed in a dialog are normalized once, by the server's `"*"` hook on insert
  (`utils/normalize_hook.normalize_doc`); the component does not reformat them.
- **When the account changes on a saved record**, `party_details` answers which of the current
  contact, billing address and site are linked to the new account **or to the record itself**, and
  the account's defaults by ERPNext's own rules (`party.py`). Then, field by field (`party.js`
  `plan_account_change`): an **empty** field takes the account's default; a **stored value is never
  overwritten and never emptied**. One linked to the new account or to the deal (a general
  contractor's job site is linked to the Opportunity only) stays quietly; any other stays with a
  note under the field ("not one of <account>'s", and *Use <suggestion>*). Review of #1291: an
  earlier rule replaced or cleared a stored site nobody had touched, on a step nobody was looking
  at, and the hand-off then copied the wrong site into the Project. ERPNext's own `party_name`
  handler, which does overwrite `contact_person` and `customer_address`, is the Desk form's client
  script; nothing runs it here, and on the server `map_fields` fills only empty fields, so the same
  rule holds for all three. An answer for values that changed while it was in flight is asked again.
  On a new record nothing is suggested: the contact and addresses are on later steps, and the
  insert's own `map_fields` fills them from the account.
- **`map_fields` and the job site** (found in review, older than this component).
  `Opportunity.map_fields` copies every empty column from the party by name, and
  `Customer.primary_address` is ERPNext's rendered address HTML, so a deal with no site took that HTML
  into its `primary_address` Link on every save: v16 checks links in `_validate_links` *before*
  `before_validate` and `validate`, and `sanitize_primary_address_link` ran only on
  `before_validate`. It now runs on Opportunity's `validate` too, after ERPNext's (hooks.py).
  Production had 28 Customers with that text and one Opportunity holding it (2026-10-14); that row
  is cleared on its next save.
- **The defaults.** Contact: flagged `is_primary_contact`, else `is_billing_contact` (ERPNext's
  `get_default_contact`), else the account's own `primary_contact` / `customer_primary_contact` when
  that Contact is linked to it (what `map_fields` copies anyway). Billing: flagged
  `is_primary_address`, else the newest (ERPNext's `get_default_address`). Site: ERPNext has none;
  `get_party_shipping_address`'s rule (flagged shipping, else the only Shipping address), and an
  account with exactly one enabled address offers it as the site too. Production had 23 Shipping
  addresses to 843 Billing on 2026-10-14, which is why a new site address starts as Shipping.
- **The two contact fields cannot disagree.** The page shows and saves `primary_contact`; the server
  keeps ERPNext's hidden `contact_person` equal to it on every save
  (`crm_enhancements/party_mirrors.py`), and `save_step` now returns what that (or `map_fields`)
  changed, so the screen shows what the record holds. `customer_address` is the visible Billing
  Address and is never mirrored onto the site.
- **Back closes the dialog first.** A quick entry is a frappe Dialog, which the router closes on any
  route change: alone, Back would close it *and* leave the step (or close the side panel). The
  dialog joins the planner kit's history guard (`planner_kit/history.js`) as an overlay, so Back
  closes the dialog and nothing else, and Save or Cancel steps back off the guard's entry. The guided
  page loads `planner_kit.bundle.js` the first time a dialog opens; where the guard cannot work,
  Back falls back to frappe's behavior.
- `party_details(doctype, party_type, party, contacts=None, addresses=None, name=None)`, GET only:
  `{"defaults": {"contact", "billing", "site"}, "linked": {"Contact": [...], "Address": [...]}}`.
  The DocType allowlist, read on the DocType, `party_type` one of the Party block's `accounts`, read
  on the account (and on the record `name`, when sent), at most 20 names of each kind, and every
  Contact and Address through `frappe.get_list` with `distinct` (a record linked twice to one party,
  as the Zoho import left some, is counted once), so user permissions and sharing decide what is
  offered. Writes nothing.

## Blocks and "more" steps (P8.1)

Some steps need more than the form's own fields. A config step names a block with `extension`;
`flow_steps` passes it through, and the record editor both shells run (`form_kit/ui/editor.js`,
*One editor, two shells* above) asks the DocType's extension (`public/js/form_kit/extensions.js`, a
registry by DocType) to draw it above the step's fields (`mount_extension`, at the end of
`render_fields`). Each shell picks the extension when a record opens (`open_extension(doctype)` in
its `load`) and drops it on `reset`, so the guided page and the side panel draw a block the same
way. The editor knows only that interface, never what the block is: `render(name, $box, ctx)`,
`on_change(fieldname, value, ctx)`, `before_create()` (a sentence that holds step 1 back, or "")
and `leave()`. `ctx` lends the record's values, `set_value` (into a field the page then saves),
`save_first` (send what the page holds) and `record_changed` (take the record's new version after
the block wrote through its own endpoint). A block that throws is caught; the fields still show.
Whatever a block creates beyond the record goes through its own whitelisted endpoints, each
checking its own permission, never through `save_step`.

A step with `more: True` is past the core of the flow: Next on the last core step offers Done,
and the rest stays a tap away (the rail, and *More details* under the step). Nothing is hidden.
That part is the guided page's own footer and rail (`ends_core`); the side panel's step picker
lists every step as before.
A section no step claims becomes a `more` step too when the config has any, so it never lands
between the core steps. `config.check` holds `more` steps after the core ones.

## The Item flow (P8.1)

TASK-2026-02790: a correctly named stock item with its supplier, price and reorder level, its bin
label printed, from a phone or a desk, without opening the Item form; a fixed-asset item lands
with its Asset. `config/item.py` walks the whole Item form (every field it shows is in one step,
held by `test_form_kit`); its core steps and their blocks (`public/js/guided_item/`):

| Step | Block | What it writes, and how |
|---|---|---|
| Name & code (creates the Item) | `item_naming`: the code helper (vendor part number as printed, `CON-<GROUP>-<DESCRIPTOR>[-<SIZE>]`, `PDT-####` and `SRV-###` from a block the person chooses) and the name builder (POL-0602's seven segments, pick lists from the catalog narrowed by the CATEGORY), with the naming guard's own preview | Item Code and Item Name, into the step's own fields |
| Description | — | the form's fields |
| Supplier & price | `item_supplier` | `api.item_flow.save_supplier`: the Item Supplier row (part number, `purchase_url`), the company's default supplier, the Item Price on the buying list for that supplier |
| Stock & bin (stock items) | `item_stock` | `save_stock`: the company's default warehouse and its Purchase reorder rule; *Print the bin label* opens `/warehouse-labels?w=<bin>`, Stock Scan's own label page |
| Fixed asset (fixed assets) | `item_asset` | `create_asset`: a draft Asset (category, location, purchase date, cost, `custom_rentable`), idempotent on the page's id in `Asset.custom_guided_client_id` |

The preview is `api.item_flow.preview_item`, which is Stock Scan's `preview_new_item`: only the
guard's two findings (a duplicate code, a name that is just the code) are shown as refusals, and
only while the guard is in force; everything else is advice. The next PDT/SRV number is
`item_flow_rules.code_blocks`: the blocks in use with each one's lowest free number, plus the first
unused block, never `MAX()+1`. The help file covers the core steps' fields; the `more` steps keep
ERPNext's own descriptions until their help is written and reviewed.

The Item itself is created by `create_record` and can never be created twice: it is named by its
code, and its `create.client_id_field` (`Item.custom_guided_client_id`, hidden and unique, as on
Opportunity) makes a resent create return the Item the first attempt made rather than a duplicate
code refusal. Item is
not where an Item opens yet (`open_by_default: False`), and the guided page's roles include
*Item Manager*, the role that creates Items.

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
5. Add the DocType to each block in `blocks.py` that it carries (and to the JS twin), with a meta
   fixture in `tests/test_form_kit_blocks.py` built the same way, so every field it names is checked.
6. Nothing to do for notes and comments: every guided DocType shows them (see "Notes and comments"
   above). Check them on the new view before it ships.
