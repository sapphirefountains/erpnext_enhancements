# Global Enhancements

Houses the **"Triton" AI-assistant** configuration (the in-app chat widget) and the **Directory Link Exclusion** doctype used by the contacts/addresses directory feature.

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `doctype/triton_assistant_settings/triton_assistant_settings.py` | Single: assistant-widget config + connection test | `TritonAssistantSettings` (pass); whitelisted `test_connection` |
| `doctype/triton_assistant_settings/triton_assistant_settings.js` | Form script: "Test Connection" button | `frappe.ui.form.on(... refresh)` |
| `doctype/triton_allowed_user/triton_allowed_user.py` | Child table: whitelisted users | `TritonAllowedUser` (pass) |
| `doctype/directory_link_exclusion/directory_link_exclusion.py` | Records a hidden Contact/Address per source doc | `DirectoryLinkExclusion` (pass) |
| `doctype/additional_supplier_group/…py` | Child table behind `Supplier.custom_additional_supplier_groups` (ported from a DB-only custom DocType in v0.8.0; see `setup/supplier_groups.py`) | `AdditionalSupplierGroup` (pass) |

## Triton AI assistant

`Triton Assistant Settings` is a **Single** holding the *widget's* behavior toggles: `enabled`, `default_model`, `request_timeout`, `enable_page_context`, `enable_write_actions`, `debug_logging`, `restrict_to_whitelist`, and an `allowed_users` child table (→ Triton Allowed User).

> **Two doctypes, one feature.** This doctype controls the **widget**. The actual gateway **connection** (Gateway URL, Admin Webhook Secret, model IDs, Twilio/Vertex secrets) lives in the separate **Triton Settings** doctype in [Enhancements Core](../enhancements_core/README.md). `test_connection` reads the connection from there via `erpnext_enhancements.triton_chat` (`get_settings`, `mint_user_token`). The form's intro HTML calls this split out — it's a common point of confusion.

The widget front-end is `public/js/global_enhancements/triton_widget.js` (loaded globally via `app_include_js`); the browser↔gateway proxy is the repo-root `triton_chat.py`. See the [public README](../public/README.md#global-ui).

## Directory Link Exclusion

Part of the aggregated **contacts/addresses directory** (see `sync_contact.py` and the [script_migrations README](../script_migrations/README.md)). Because a Contact/Address can be surfaced on a document *indirectly* (through a related party), "unlinking" one can't simply delete a link without orphaning it elsewhere. Instead, a `Directory Link Exclusion` row records **"hide ref X from source Y"**:

- `ref_doctype` / `ref_name` — the hidden Contact/Address (stored as plain **Data**, not Link, so deleting the Contact is never blocked).
- `source_doctype` / `source_name` — the document it was hidden from.

`sync_contact.cleanup_directory_exclusions` garbage-collects these rows on `on_trash` of Project, Master Project, Address, Opportunity, Contact, Supplier, and Customer (constant `EXCLUSION_DOCTYPE` in `sync_contact.py`). Because references are plain Data, there's no DB cascade — hence the explicit cleanup.

## `hooks.py` touchpoints

- `triton_assistant_settings.js` auto-loads as the doctype's own form script (no `doctype_js` entry needed).
- Triton widget assets (`triton_widget.css` / `triton_widget.js`) load globally via `app_include_css`/`app_include_js`.
- `Directory Link Exclusion` rows are pruned by `sync_contact.cleanup_directory_exclusions` on `on_trash` of the party doctypes.
- The global `doc_events["*"]["after_save"]` → `utils.triton_sync.global_triton_sync` notifies the assistant of every save (see [script_migrations / root utils](../script_migrations/README.md)).

## Gotchas

- `test_connection` is gated by `frappe.only_for("System Manager")` and returns `{ok, message}` rather than raising.
- `directory_link_exclusion.py` uses **tab** indentation while the two Triton files use **4-space** — match the file.
