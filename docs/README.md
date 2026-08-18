# Documentation index

Documentation for this app lives in two places, and knowing which is which saves time:

- **Next to the code.** Every module directory carries a `README.md` that maps its files,
  its DocTypes, and the decisions behind them. That is the primary reference — start there.
- **Here in `docs/`.** Cross-cutting design documents, subsystem deep-dives, and the
  migration runbooks.

The root [`README.md`](../README.md) has the module map and the full index of module
READMEs. [`CLAUDE.md`](../CLAUDE.md) carries the gotchas worth knowing before you touch
anything.

## In this directory

| Document | What's inside |
|---|---|
| [development.md](development.md) | Local setup, the three ways tests run, linting, deploys, and where a change goes |
| [KPI_DASHBOARD_DESIGN.md](KPI_DASHBOARD_DESIGN.md) | The full KPI catalogue: every metric, its definition, source doctypes, and target |
| [PRODUCT_CONFIGURATOR.md](PRODUCT_CONFIGURATOR.md) | The configure-to-order product model and its pricing rules |
| [FLEET_VEHICLE_MAINTENANCE.md](FLEET_VEHICLE_MAINTENANCE.md) | Fleet vehicle maintenance scheduling |
| [DOCUMENT_MERGE.md](DOCUMENT_MERGE.md) | Duplicate document merging and its fail-closed philosophy |
| [email-design-system.md](email-design-system.md) | The one email layout, its components and palette, how to add an email, and an inventory of every email the app sends |
| [UX_QUICK_ENTRY_AND_FORM_LAYOUTS.md](UX_QUICK_ENTRY_AND_FORM_LAYOUTS.md) | Quick Entry and form-layout conventions |
| [attribution-runbook.md](attribution-runbook.md) | Lead attribution: the settings, the website ingress payload contract, and the one tickbox that turns enforcement off |
| [website-capture/](website-capture/) | The WordPress half: the first-touch UTM mu-plugin, the Fluent Forms hidden fields and webhook mapping, and why a JS-injected field never reaches ERPNext |
| [error-log-runbook.md](error-log-runbook.md) | Every Error Log signature that no code change can fix — dead MDM credentials, missing Google grants, the SMTP relay IP, log retention — with the commands in Bash and PowerShell |
| [field-photos-and-payroll-runbook.md](field-photos-and-payroll-runbook.md) | The job-photo capture gate, where photos are routed, and the Shaw & Nielsen payroll workbook |
| [marketing-spend-runbook.md](marketing-spend-runbook.md) | Loading marketing spend, the value-stream dashboard, and the four KPIs that were measuring a dead column |
| [marketing-platform-plan.md](marketing-platform-plan.md) | The `marketing/` module build plan: why WP-1 and WP-4 are starved of input, the platform approvals that are the real critical path, and the four phases |
| [marketing-platform-approvals.md](marketing-platform-approvals.md) | Phase 0: the six external approval packets, the pre-flight checklist, the quota sheet, and the LinkedIn filing order that a rejection makes expensive |
| [industry-type-proposal.md](industry-type-proposal.md) | Proposed keep/merge/retire list for the 89 Industry Type values, from live usage counts. A proposal — nothing executed |
| [opportunity-field-guide.md](opportunity-field-guide.md) | Field-by-field guide to the Opportunity form |
| [pick-routing-map-po-details.md](pick-routing-map-po-details.md) | Spike: three ways to show PO item detail on the Pick Routing Map, and why the inline one wins |
| [workspace-sidebars.md](workspace-sidebars.md) | The `Workspace Sidebar` records, and why Frappe's importable app-level directories may contain nothing but JSON |
| [pdf-generation.md](pdf-generation.md) | Why PDF generation fails on both backends, and the runbook to fix the host |
| [procurement-tracker-map.md](procurement-tracker-map.md) | The Project form's Procurement Tracker: what renders it, the chain SQL underneath, and where its status/quantity arithmetic lives |
| [stripe_surcharging_compliance.md](stripe_surcharging_compliance.md) | The surcharging compliance checklist gating OD-7 |
| [migration/](migration/) | Chart-of-accounts design, mapping workbooks, and the per-work-item apply runbooks |

## Planning and decisions

| Path | What's inside |
|---|---|
| [`../PLAN.md`](../PLAN.md) | The ERPNext migration master plan — dependency graph, critical path, work-item index, risk register |
| [`../work-items/`](../work-items/) | One file per `WI-xxx` work item: why, native-first check, scope, acceptance criteria, rollback |
| [`../decisions/OPEN-DECISIONS.md`](../decisions/OPEN-DECISIONS.md) | The `OD-n` register of open **business** decisions |
| [`../decisions/adr/`](../decisions/adr/) | Architecture decision records — why the codebase is built the way it is |

## Keeping documentation accurate

Every change bumps the version and adds a `CHANGELOG.md` entry. Beyond that:

- A new file needs a row in its module README's file map.
- A new customization needs a line in `hooks.py` **and** an entry in the owning module's
  README.
- A new patch needs a row in [`patches/README.md`](../erpnext_enhancements/patches/README.md)
  and a line in `patches.txt`.
- A decision that a future reader would find surprising needs an ADR.
