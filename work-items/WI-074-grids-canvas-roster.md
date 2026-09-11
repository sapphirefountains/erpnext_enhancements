# WI-074 — Role grids, the canvas port, and an audit roster that can be honest about dates

**Status:** in progress
**Branch:** `claude/hr-grids-canvas-roster` (off `main` at v1.395.1)
**Tracked:** PRJ-00616
**Follows:** [WI-072](WI-072-hr-module-and-training-redesign.md) (the three items it deferred) and
[WI-073](WI-073-hr-safety-and-competency.md)

## Why

WI-072 shipped ten deliverables and deferred three, each for a stated reason. A nine-agent mapping
pass checked all three reasons against the code and against production. **Two of the three were
wrong, and the third was overstated.**

**Per-role home grids were called impossible.** WI-072's reasoning: "every active employee holds
roughly thirty roles", so role-gating achieves nothing. That reasoning is about `Workspace.roles` —
and it is the wrong gate. Core v16's **`Desktop Icon` has its own `roles` child table**, and
`get_desktop_icons()` intersects it with `frappe.get_roles(user)` before boot returns the grid
(`desktop_icon.py:182,:200`; `boot.py:65`). This app already stamps 34 tiles on every migrate. The
work is **one unset field on rows we already write** — data, not code. Separately, prod says no role
at all is held by all 20 System Users, and the `* Team` roles discriminate cleanly.

**The audit roster was called "small work, the data all exists now".** True for the *live* question,
false for the one an insurer asks. An as-of-date answer is reconstructible for credentials,
sign-offs, policy signatures and session attendance — and **structurally impossible** for
certificate revocations, position/tier, work restrictions and PPE. The dividing line is not the
doctype: it is whether a fact is stored as a **date** or as a derived **`status` string**, because
every status transition in both modules is written with `db_set(..., update_modified=False)`, which
bypasses `save_version()`. So `track_changes = 1` — set on essentially every doctype here — is *not*
a fallback. It returns nothing for exactly the transitions an auditor asks about, and plenty for
harmless field edits.

**The canvas port was sized at ~2,600 of 3,842 lines.** The real unported gap is ~2,156 (55%, not
68%), and it is lumpy: the learner preview cluster is ~640 lines and checkpoints ~500, so two areas
are half the total.

And the mapping found something nobody was looking for: **the canvas corrupts lesson content on
every edit.** See below.

## The live defects (package A, ships first and alone)

1. **`training_canvas.js:602` runs stored HTML through `frappe.utils.xss_sanitise` — an HTML
   *escaper*, not a sanitiser — and `:714` writes the result back into `content`.** One keystroke in
   a Rich Text block permanently turns a lesson's markup into entity soup the learner reads as
   literal tags. It is invisible downstream: `sanitize_html` short-circuits when BeautifulSoup finds
   no element, so the all-entities string passes through unchanged. `tests/test_training_canvas.py`
   has 27 tests in CI and **none does a rich-text round trip**, so it is green today. The classic
   builder only *previews* through the same function, which is why it never showed there.
2. **Canvas `remove_block` orphans checkpoints permanently.** Checkpoints hang off `block_key`; the
   classic deletes them on block removal (`training_builder.js:1747-1757`), the canvas does not
   (`:1110-1117`), and `create_draft_version` clones the orphans into every later version.
3. **Cancelling a `Training Session` leaves its minted completions `Valid`.** The withdrawal throws
   on a required field into a swallowed Error Log, so the company holds certifications for a talk it
   has formally said did not happen.
4. **`certificates._revoke_certificates_for` overwrites `expires_on` with `today()`**, destroying
   the validity window printed on the certificate the client is holding. **Moved to package C**
   after review: that clobber is currently the *backstop* stopping a failed `cancel()` from
   resurrecting a revoked certificate as Valid (`_derive_status` resurrects any row whose
   status is Revoked while `docstatus != 2`). Removing it needs a `revoked_on` field for
   `_derive_status` to read — which is exactly what C adds. Fixing it in A alone would have
   shipped a worse bug than the one it fixed.

## Decisions (Nik, 2026-09-11 — do not re-litigate)

1. **Fill the six empty Hub workspaces, do not delete them.** They are his records.
2. **Create a `Support Hub`** — Support Team is the largest on the site at 13 and had none.
   `Executive Hub` and `Finance Hub` already exist, populated and role-gated; they are the template,
   not work. (An earlier claim of mine that all three were missing was wrong.)
3. **The HR module gets its own icon, gated to those who should use it.**
4. **Full canvas port, retire the classic builder.**
5. **The roster takes an as-of date, and the dated gaps get fixed first** — schema plus backfill
   before the export is written.

## Guardrails

- **`Desktop Icon.roles` is show/hide, never a permission boundary.** Typing the route still works.
  It must not be described, or relied on, as access control.
- **Zero icons in frappe or erpnext populate `roles`.** The enforcement path is real and readable but
  unexercised upstream, so it needs its own regression test rather than trust.
- **Workspaces are not fixtures.** Module-folder JSON, timestamp-gated, skipped in **silence** — and
  a successful import writes the file's stamp onto the row, so file and row stay equal forever after.
  Every change needs a bumped `modified` **and** a `reload_doc(..., force=True)` patch.
- **Giving a hub `module` + `app` is irreversible in practice**: `remove_orphan_entities` force-deletes
  a `public=1` workspace with both set once no `<app>/**/workspace/**/*.json` carries its name. The six
  empty hubs have neither, so core will never sweep them either.
- **A row that cannot be reconstructed historically must be marked as such**, never silently given
  today's value. That is the whole difference between an audit artefact and a misleading one.
- **`Expiring` counts as HELD** (90-day horizon, relative to the *as-of* date, not to today).
  **`Supervised Only` is not competence** and must never read as cleared to work alone.
- **A historical roster must include leavers.** Every enumeration in the app filters
  `Employee.status = 'Active'`; core's `date_of_joining` / `relieving_date` answer it and are used
  nowhere. Leavers are exactly who an insurer asks about after an incident.
- Do not port the canvas onto core's EditorJS: it stores a JSON blob, while `block_key` here is a
  relational identity that learner progress and checkpoints hang off.
