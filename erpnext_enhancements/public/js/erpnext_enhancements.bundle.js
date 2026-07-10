/*
 * Global desk scripts — single esbuild bundle (content-hashed filename).
 *
 * Same rationale as kanban.bundle.js: raw /assets paths are served with a
 * 1-year immutable Cache-Control and carry no content hash, so an edit to any
 * of these files never reached a device that had already cached it (only
 * hard-refreshed desktops saw fixes). Bundle filenames are content-hashed via
 * assets.json, so every deploy gets a fresh URL on every device.
 *
 * Import order mirrors the old hooks.py app_include_js order. Every file is a
 * self-contained classic script (IIFE and/or explicit frappe.provide /
 * window.* assignments). The two files with top-level declarations
 * (erpnext_enhancements.js, activity_log_numbering.js) were audited
 * repo-wide: nothing outside each file references those identifiers, so
 * module-scoping them is safe.
 *
 * NOT in this bundle (deliberately):
 *   - vue.global.js and project_enhancements/lib/frappe-gantt.umd.js: vendored
 *     UMD/global builds that must define window.Vue / window.Gantt. Importing
 *     a UMD file from a bundle makes esbuild capture its exports instead of
 *     setting the global — and their content never changes, so the immutable
 *     /assets cache cannot serve them stale. They stay raw includes, listed
 *     BEFORE this bundle in hooks.py.
 *   - the Kanban patch suite: see kanban.bundle.js.
 *   - doctype_js / lazy dashboard components: loaded on demand through
 *     frappe.require, which has a version-aware client-side cache.
 */
import "./erpnext_enhancements.js";
import "./global_comments.js";
// Comments App: comments.js defines erpnext_enhancements.render_comments_app
// (it reads window.Vue only at call time, behind a guard); comments_auto.js
// mounts it on every doctype in COMMENT_APP_DOCTYPES — doctypes whose own form
// scripts call render_comments_app are deliberately excluded there.
import "./comments.js";
import "./comments_auto.js";
import "./crm_note_enhancements.js";
// Closed Won -> "Create project now?" prompt (global listener; works on the
// Opportunity form and the Kanban board). Server half: crm_enhancements/project_prompt.py.
import "./crm_enhancements/create_project_prompt.js";
import "./performance_fixes.js";
import "./activity_log_numbering.js";
import "./filter_help.js";
// Injects a "DocTypes" navigation section into the desk Global Search results
// page (frappe.searchdialog), so DocTypes typed into the awesomebar and opened
// with Enter stay reachable. Complements the awesomebar live-search patch in
// erpnext_enhancements.js. See the file header for the full rationale.
import "./global_enhancements/global_search_doctypes.js";
// Field help text as a hover "ⓘ" icon (gated by frappe.boot.ee_field_description_icons)
import "./global_enhancements/field_description_icons.js";
// Contact/Address quick-entry dialogs + in-place directory refresh (gated by
// frappe.boot.ee_contacts_ux; must be global — list/awesomebar/link-field
// create paths fire outside any doctype_js). Server: contacts_ux.py.
import "./global_enhancements/contact_address_quick_entry.js";
import "./telephony_client.js";
// global_enhancements
import "./global_enhancements/quill_mentions.js";
import "./global_enhancements/global_sidebar.js";
// Drops localStorage sidebar picks that point at deleted Workspace Sidebars
// (the desk trusts them blindly and never validates them)
import "./global_enhancements/sidebar_pref_heal.js";
import "./global_enhancements/auto_collapse_sidebar.js";
import "./global_enhancements/unlink_and_delete.js";
// Generic document merge — global "Merge into…" form button + list bulk merge
// (gated by frappe.boot.ee_merge_tool). Server: document_merge.py.
import "./merge_tool/merge_tool.js";
// Sapphire Fountains Mermaid theme (window.sf_mermaid) — before its consumers
// (triton_widget.js here; process_document.js via doctype_js loads later).
import "./global_enhancements/mermaid_theme.js";
import "./global_enhancements/triton_widget.js";
// project_enhancements (task_tree_manager / column_selector / gantt_zoom are
// preloaded globally so doctype scripts and lazy dashboard tabs can use their
// erpnext_enhancements.* namespaces immediately)
import "./project_enhancements/task_tree_manager.js";
import "./project_enhancements/dashboard_components/column_selector.js";
import "./project_enhancements/dashboard_components/column_resizer.js";
import "./project_enhancements/gantt_zoom.js";
// Live collaborative form sync (COLLAB_DOCTYPES allowlist inside)
import "./collab/live_form_sync.js";
