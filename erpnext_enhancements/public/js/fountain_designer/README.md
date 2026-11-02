# Fountain Designer page (phase F)

The guided single-page designer at `/desk/fountain-designer`. The plan is
[`docs/fountain-designer/PLAN.md`](../../../../docs/fountain-designer/PLAN.md), sections 2, 3.3, 3.4
and 3.5 (draft PR #1389 until it merges). The tracker is TASK-2026-03018 on PRJ-00580.

| File | What it does |
|---|---|
| `water_engineering/page/fountain_designer/fountain_designer.js` | The Desk page. It loads the bundles, mounts the controller, and passes `on_page_show` on. |
| `public/js/fountain_designer.bundle.js` | The entry point. It installs `window.sf_fountain_designer` (`mount` and the pure modules). |
| `app.js` | The controller. It holds the place, the model on screen, the live derive and the autosave, and handles every event (delegated on `data-fd-*`). |
| `model.js` | The schema-2 model helpers: paths, edits that pin a section, policies, presets, `merge_derived`. |
| `router.js` | The step lists, plus the address `?design=&step=&demo=`: read, written, normalized. |
| `api.js` | The endpoint names and arguments, the debounced derive with its stale-reply guard, and `debounce`. |
| `verdict.js` | The verdict panel and the phone strip. |
| `chart.js` | The SVG allow-list for the charts the server draws. |
| `ui.js` | Fields, cards with the setup policy and the Advanced reveal, and tiles. |
| `steps/*.js` | One module per step: `start`, `site`, `features`, `basins`, `pump_room`, `review`, `outputs`. |
| `demo/` | The fixture (`*.json`) and `demo.js`, which answers every call in the browser. It ships as `fountain_designer_demo.bundle.js` and is fetched only with `?demo=1`. |
| `public/css/fountain_designer.bundle.css` | The styles. Every selector is `.fd-`, and every color is an `--sf-*` token. |

Tests:
- `scripts/test_fountain_designer.mjs` runs under node.
- `erpnext_enhancements/tests/test_fountain_designer.py` holds the static checks and runs the node harness.
- CI runs them as their own step.

## How it behaves

- **Two modes, one page.**
  - Quick shows Start, Site, Features, Review and Outputs. Features holds only the kit's headline inputs, and every section except the features is `auto`.
  - Full shows all seven steps, with a setup-policy select and an Advanced reveal on each card.
  - `model.set_mode` switches between them. Going to quick puts every section back on `auto`. When any section was set by hand (custom or a preset), the page asks first, naming the sections, because the next derive replaces them and the autosave keeps that.
- **The site.**
  - The engine fills the looked-up site fields (`model.SITE_LOOKUPS`) only where they are blank. The model does not record which of them a person typed.
  - In quick mode, where they are never inputs, a new address blanks them, so the next derive looks them up for the new address.
  - In full mode, the Site card's Advanced has **Refresh from the address**, which blanks them all, typed ones too.
- **A design not made here.** A stored design whose `model` is not schema 2 (an old wizard record, or one started in the form) is never opened as a blank design, whose first autosave would overwrite it. The page says so and offers the form and All designs.
- **Live results.**
  - Every edit calls `derive(model_json)` once the person has paused for 400 ms.
  - Only the reply to the newest model is drawn. A reply to a model edited since it was sent is dropped, and so is its error.
  - `merge_derived` writes the reply's model into `auto` sections only:
    - a section the person edited becomes `custom`, and a preset stays as chosen;
    - site fields fill only where they are blank.
  - Derived values and the inputs of `auto` sections are repainted in place. The step is redrawn only when a row comes or goes, so typing is never interrupted.
- **Saving.**
  - The design saves 1.5 s after the last edit, and at once on a step change or when another design opens.
  - A save still waiting when the person goes to another Desk page runs anyway: a Desk page stays in memory.
  - The footer says Saved, Saving, Unsaved changes, or Not saved (with Try again).
  - Closing or reloading the tab with unsaved edits asks first.
  - A push and a sign-off wait for every edit to be saved first, the one in flight included: a push builds from the stored BOM, and a save that lands after a sign-off must not carry the old acks. A failed save sends neither. After a sign-off, the model (with the new acks) is saved once more.
- **The address.**
  - The address is `/desk/fountain-designer?design=<name>&step=<key>`, with `&demo=1` in demo mode.
  - Every step and every design opened is a history entry, so Back and Forward work, and a reload or a link lands on the same place.
  - The page reads its place from `frappe.route_options`, consuming the keys, on every `on_page_show`. It writes its own address with `pushState` / `replaceState` on `location.pathname`, like the Maintenance Day Board.
  - Every entry stands for its own place. An entry with no `design`, `step` or `demo` key is the list on Start, out of the demo, so Back onto the bare address the page was opened at lands on the list, and a return from another Desk page to the bare address leaves the demo (`router.place_from`).
- **States.**
  - Every load has a loading, empty, error and no-access panel, drawn by sf_kit.
  - When the designer's server side is missing, the error panel offers **Open the demo**.
- **Access.** The page is gated like the old wizard: `api.water_design.check_permission` (read on Water Feature Design), in demo mode too. What a person may do comes from `boot().can`.
- **Keyboard.**
  - The step rail and the tiles move with the arrow keys. Tiles are a radio group.
  - The catalog picker is a combobox: Down opens it, the arrows choose, Enter picks and Esc closes.
  - Focus moves to the step's heading when the step opens.
- **Phone width.**
  - The rail becomes a bar across the top that scrolls to the current step.
  - A strip of chips and the budget sits above the step, and the verdict follows it.
  - The footer with Back and Next sticks to the bottom.

## The contract

This is the contract **phase E's `water_engineering/api/designer.py` must honor**. Each endpoint is
whitelisted under `erpnext_enhancements.water_engineering.api.designer.`. The page calls each one
through `sf_kit.call(..., { quiet: true })`, form-encoded like `frappe.call`:

- `boot`, `get_design` and `catalog_search` go as **GET**, so they must not be whitelisted `methods=["POST"]`.
- Every other call goes as POST.
- A `frappe.throw` reaches the person in its own words. Write those words for a person.

The fixture in `demo/` is the reference instance of every shape below. `scripts/test_fountain_designer.mjs`
checks that the fixture carries every key named here. If E changes a shape, change the fixture and this
section in the same PR.

### `boot()` → object

| Key | Shape | Used for |
|---|---|---|
| `schema` | `2` | The model version the page writes. |
| `units` | `"US"` | Labels. |
| `can` | `{read, write, create, push_quotation, push_material_request, email}`, booleans | `create` shows New design. `write` allows edits, saves and sign-offs. The two `push_*` keys enable the Outputs buttons. `email` (the design's email permission) enables Send design. |
| `kits[]` | `{key, label, family, venue_type, desc, headline[], model}` | The Start tiles. `model` is the kit's starter model (schema 2). It is copied and stamped with `kit` and `mode`. |
| `kits[].headline[]` | `{path, label, kind, unit, min, max, step, options[]}` | Quick mode's Features inputs. `path` is a model path (`features.0.inputs.length_ft`). `kind` is `number`, `int` or `tiles`, and `tiles` takes `options[] = {value, label, desc}`. Each path must exist in the kit's model. |
| `families[]` | `{key, label, product_class, types[], inputs[], looks[]}` | Full mode's feature cards. **Add a feature** uses the family of the design's first feature, else the first family here, so put the most common family first. A blank design starts with one feature of that family and one basin. `inputs[] = {key, label, kind, unit, options[], advanced}`, written to `features.N.inputs.<key>`, and `advanced: true` puts it behind Advanced. `looks[] = {value, label}`, written to `features.N.effect.look`. `product_class` is the catalog class for the product picker. |
| `sections[]` | `{key, label, presets[]}`, one per setup section, **in ladder order**: `features`, `basin`, `piping`, `pump`, `control`, `bypass`, `surge`, `treatment`, `lighting`, `electrical`, `winterization` | The policy select. `presets[] = {key, label, desc, values: {path: value}}`. A preset is applied by setting each path, and a section on a preset whose values no longer match shows Custom. |
| `options` | `{venue_types[], jurisdictions[], basin_shapes[], pump_locations[] ({value,label}), materials[], controls[] ({value,label}), pump_classes[], sanitizers[], lighting_controls[], service_voltages[] (numbers), lines[]}` | The selects. A list of numbers stores numbers. |
| `catalogs.equipment_classes[]` | `{key, label}` | The catalog picker classes. |
| `catalogs.slot_classes` | `{<equipment slot>: <class key>}` | The class each `model.equipment[].slot` searches: `filter`, `uv`, `chlorinator`, `light`, `transformer`, `bypass`, `surge_arrestor`, `autofill`, `vfd` and `prv` (the page's `model.SLOT_SECTION`). A slot with no class here gets a plain item-code field, never a search of another class. The pumps search `pump`, and a feature's product searches its family's `product_class`. |
| `house_rules[]` | `{key, label, value, unit, citation}` | The constants listed on the page (PLAN.md 2.3). |
| `prints[]` | `{key, label, print_format}`, one per paper whose Print Format exists on the site (phase G): the keys of `outputs.schedules[]`, plus `packet` | The Outputs step links each paper of a **saved** design to `/printview?doctype=Water%20Feature%20Design&name=<name>&format=<print_format>&no_letterhead=1` (the formats draw their own letterhead). An unsaved design says "save the design to print it"; a key with no entry says "not available yet". |

### `get_design()` and `get_design(name)`

- **With no `name`:** returns `{designs: [...]}`, the designs the caller may read, newest first, for Start's list. Each row is
  `{name, design_title, kit, mode ("Quick"|"Full"), status, family, project, opportunity, customer,
  blocker_count, warning_count, issue_ready (0|1), budget_total, bom_subtotal, modified}`.
- **With `name`:** returns `{name, design_title, mode, status, kit, project, opportunity, customer,
  docstatus, can_write, modified, model}`.
  - `model` is the stored `model_json`, parsed.
  - `can_write` is false for a person without write and for an Issued (submitted) design. The page is then read only.
  - A missing design is a 404 (`DoesNotExistError`). The page shows "not found" with a way back to the list.

### `derive(model_json)` → reply

The call is stateless, and the page calls it on every edit. `model_json` is the schema-2 model as JSON text.

| Key | Shape |
|---|---|
| `model` | The derived copy of the model. Every `auto` section is filled, and `custom` and `preset` sections come back untouched. Site fields the address fills: `lat`, `lng`, `elevation_ft`, `air_temp_f`, `water_temp_f`, `frost_depth_in`, `hardness_ppm`, `kwh_rate`, `water_rate_per_ccf`. |
| `results` | `{<calc>: CalcResult}`. These are the envelope dicts (`value, unit, formula, status, citations[], warnings[]` …), shown nowhere yet and kept for the drawers. |
| `values` | The derived values per row, keyed by row id. **These are the numbers the steps show beside the inputs.** `features.<id>`: `{flow_gpm, head_ft, note}`. `basins.<id>`: `{volume_gal, weight_lb, surge_in, freeboard_in}`. `segments.<id>`: `{velocity_fps, loss_ft, size}`. `pumps.<id>`: `{flow_gpm, head_ft, kw, npsh_margin_ft, bep_pct}`. A key may be null. |
| `rollups` | `{design_flow_gpm, operating_flow_gpm, tdh_ft, operating_head_ft, selected_pump, selected_pump_label, pump_kw, total_basin_gallons, budget_total, bom_subtotal, om_cost_per_year, blocker_count, warning_count, completion_percent, issue_ready}`. These are the PLAN.md 3.2 rollup fields. `selected_pump_label` is what the panel prints. `bom_subtotal` is the sum of `bom[].amount`, and `budget_total` is that plus the house-rule contingency (`budget_contingency_pct`): the Budget the Budget Proposal and the Design Report print (phase G). The Outputs step's BOM foot shows the subtotal, the contingency and the budget. |
| `issues[]` | `{key, code, severity, title, detail, section, step?, path?, fix_hint, citation, remedies[], acknowledged}` (more on this below). |
| `readiness` | `{sections[], calc_ready, issue_ready}`. Each entry of `sections[]` is `{key, label, status, missing[]}`, where `status` is `ok`, `warn`, `blocked`, `missing` or `n_a`, and `missing[]` holds strings. |
| `verdict` | `{level: "good"\|"warn"\|"bad", headline, package_ready}`. `package_ready` means no blockers and every warning signed off. |
| `bom[]` | `{section, item, description, qty, uom, rate, amount, source ("Auto"\|"Manual"), catalog_source}`. These are the Water Feature Design Line fields. |
| `charts` | `{duty_svg, schematic_svg, oneline_svg}`. Each is SVG text or null (more on this below). |
| `outputs` | `{schedules[], packet, om, winterization, startup[]}` (more on this below). |

**Issues.** These are the `issues.py` dict with three additions:

- **`section` and `step`.** `section` is one of the setup sections above, or `site`. The page finds the step that fixes an issue from it (`basin` → Basins, `piping` / `pump` / `control` / `treatment` / … → Pump room). The issues.py v1 names `basins`, `tiers`, `nozzles`, `pipe` and `pumps` are understood too. An issue may name its `step` outright.
- **`path`** (optional). The model path to focus when the person follows "Go to …".
- **`remedies[]`**, each `{label, set: {path: value}}`. A remedy is one click: the page applies each path as an edit, which pins its section `custom`, and derives again. For an issue with no one-click fix, send an empty list.
- **`acknowledged`.** For a warning someone signed off, this is `{note, by, on}`. Otherwise it is null.

`key` must stay stable across derives, so that an acknowledgement survives them (the issues.py rule).

**Charts.** Draw the SVG in `currentColor`, with a `viewBox` and no fixed pixel size. The page's CSS colors these classes and leaves everything else as drawn: `fd-curve-pump`, `fd-curve-system`, `fd-point` and `fd-bep` (the BEP window, a filled rect). The chart's text may carry `fd-label` and its grid lines `fd-grid`; the page's form rules of those names are written `.fd-label:not(text)` and `.fd-grid:not(line)` so they never reach into a chart (v1.769.0). A scoped override under `.fd-chart` cannot do it, because any CSS `font-size` or `font-weight` outranks the SVG attribute it would have to restore. The page also passes the text through an allow-list (`chart.js`):

- Drawing elements only: no `<script>`, `<foreignObject>`, `<image>`, `<a>` or `<use>`.
- No `on*` attribute and no `style`.
- No `url()` except `url(#id)`.
- No `xlink` namespace.

A chart that breaks a rule is not drawn at all. Read `chart.js` for the full list.

**Outputs.**

| Key | Shape |
|---|---|
| `schedules[]` | `{key, label, url}`. The keys are `water_engineering/prints.py`'s `OUTPUTS`. `url` stays null: the derive is stateless and does not know the design's name, so the page builds the link from `boot.prints[]`. Only a site-relative `/desk/…`, `/app/…`, `/printview…` or `/api/method/…` link becomes a link. |
| `packet` | `{label, url}`, or null. |
| `om` | `{total_per_year, lines[]}`, where each line is `{label, amount}`. |
| `winterization` | `{needed, steps[]}`. `steps[]` are the titles of the winterization module's checklist (`engine.families.winterization`). |
| `startup[]` | Strings. |

### `save_design(name, model_json, fields_json)` → `{name, status, mode, modified, design_title}`

- **`name`.** Null creates the design. This is the Create button on Start.
- **`model_json`.** The schema-2 model as JSON text.
- **`fields_json`.** The record's own fields: `{design_title, project, opportunity, customer}`.
- **What the server does.** It re-derives, then writes the rollups, the `bom` rows and the JSON fields (PLAN.md 3.4).
- **Concurrency.** The page saves one request at a time. The last write wins; there is no optimistic lock yet.

### `push_quotation(name, quotation=None)` and `push_material_request(name)`

Each returns `{doctype, name, url, lines, text_lines}`:

- `lines` counts the item lines.
- `text_lines` counts the lines with no Item.

The page links to `/desk/<doctype-slug>/<name>`. The demo answers with `demo: true` and creates nothing.

### `catalog_search(equipment_class, q)` → `[...]`

Each row is `{item, item_name, description, generic, source, equipment_class}`:

- `equipment_class` is one of `boot.catalogs.equipment_classes[].key`.
- `generic` is the generic-estimate flag (PLAN.md 3.2).
- `source` is `datasheet`, `digitized`, `rep-supplied` or `generic estimate`.

The page shows up to 12 rows.

### `email_draft(name, print_format, attach_pdf)` → `{subject, recipients, message, print_format, formats[], attachment}`

Phase G, Send design. POST. The start of an email that sends one paper of a **saved** design.

- **`print_format`**: one of `boot.prints[].print_format`. Blank means the Design Report.
- **`subject`**: `<design title>: <paper> (#<name>)`.
- **`recipients`**: the customer's email, found the way the prints find the party (`print_lookup.party_details`), or `""`.
- **`message`**: the starting text, as plain paragraphs: the composer is a rich-text editor and strips the email shell. The shell (`email_style.wrap`, the Design pillar) goes on when the email is sent, in a Communication `before_insert` hook (`wrap_design_email`).
- **`formats[]`**: `{print_format, label}` for every paper this site has (the same filter as `boot.prints`).
- **`attachment`**: with `attach_pdf` 1, the paper as a private PDF File on the design (`{name, file_name, file_url}`); otherwise null. The one made since the design last changed is reused.
- **What the page does with it.** It opens frappe's `CommunicationComposer` on `{doctype, name}` with the file attached and ticked. The person sends the email from there, and the composer links it to the design's timeline. The Desk form instead uses the composer's own Attach Document Print.
- **What the server refuses.** It refuses without the email permission, and refuses a format that is not one of the design's papers. The demo answers `{demo: true}` and sends nothing.

### `acknowledge_issue(design, issue_key, note)` → `{acks: [{issue_key, note, by, on}]}`

- **What it does.** It signs off one open **warning**, and the page re-derives afterwards.
- **What the server refuses.** It refuses (417) an issue that is not open, an issue that is not a warning, and a design that is not a draft. The page shows the server's words.
- **Where the sign-offs live.** They live in `model.acks` as well as on the record's Water Design Issue Ack rows, so a derive can mark `issues[].acknowledged`.

### Additions to the schema-2 model (PLAN.md 3.3)

The page writes four things PLAN.md 3.3 does not list. Phase C's `engine/model.py` `validate()`
must accept them, and E must store them as they are:

1. **`setup[<section>]` may be `"preset"`**, beside `"auto"` and `"custom"`. With `presets` (item 2), the page can show which preset is on and turn the section Custom when the values move.
2. **`presets: {<section>: <preset key>}`.** This is the preset chosen per section.
3. **`pump_room: {location: "vault"|"room"|"submersible", distance_ft, elevation_ft, route_template, material}`.** These are the Pump room step's own inputs, which the route template reads (ladder step 6). They belong to the person, so no derive overwrites them.
4. **`winterization: {}`.** This is the winterization section's model, filled by the engine when `auto`.

The page also reads `pumps[].label` (a pump's display name) and `equipment[].auto` (false once the person has edited the row) as PLAN.md writes them.

## After phase E

`api/designer.py` is on the site from v1.768.0, and the page's default path calls it; `?demo=1` stays
the labeled training demo.
- **Where each part is drawn.** `water_engineering/derive.py` builds every derive reply. The charts
  (v1.769.0) come from `engine/drawings.py`:
  - `pump_duty_svg` draws the duty point;
  - `page_svg` makes the print schematic and one-line safe for the page's allow-list.
- **Tests.** `tests/test_designer_derive.py` holds a real reply to this contract, key for key, and runs
  it through `model.merge_derived`, `verdict_html` and `chart.safe_svg` under node.
- **Not editable on the page yet.** `boot().families[].inputs` leaves out the families' table inputs (a
  cascade's `tiers`, an interactive feature's `play_features` and `sequence`), because `ui.js` has no
  table field. The kits carry them in their models, and the derive reads them. A table editor is page
  work for later.
- **Phase H's two jobs.**
  - The error panel's second sentence ("Until the designer's server side is installed, the demo shows
    how it works.") and its **Open the demo** button (`app.js`, `failed()`) no longer fit. Phase H
    rewords the sentence and moves the demo to a link meant for training.
  - The Dock row on the Design profile now opens a working page.

## Demo mode

Open the page with `?demo=1` to see it work: the head says **Demo data**, and every designer call
(not the access check) is answered in the browser by `demo/demo.js` from the fixture. Three designs are already on the
list:

- **DEMO-LOBBY**, a hotel lobby sheet wall, 8 ft, quick mode. It has two warnings: the trough surge, with a one-click remedy, and the Darcy against Hazen-Williams spread. Its budget is $36,506.
- **DEMO-PLAZA**, a plaza interactive with 24 jets, full mode. Its blocker is "interactive features need UV", which the remedy and the Chlorine, UV and sand filter preset both clear. It also has a suction-velocity warning. Its budget is $169,320.
- **DEMO-POND**, a one-acre pond aerator, quick mode. It is good to quote with no warnings, and its budget is $8,670.

The demo is a little alive, and none of it is engineering:

- the kit's headline inputs scale the flows, heads, power and budget;
- the duty chart is redrawn at the new point;
- a remedy clears its issue;
- signing off the warnings makes the package ready.

New designs and saves last for the tab (`sessionStorage`). Keep demo mode after phase E: it is the
training mode.

To regenerate the fixture, edit the JSON by hand. Keep `bom_subtotal` and `budget_total` equal to the BOM's sum, the demo carrying no contingency (the
node test checks it).
