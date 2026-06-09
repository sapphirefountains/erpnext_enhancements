/**
 * @file This script enhances the standard Frappe Task list's Gantt view.
 *
 * Targets: the Task DocType list view (Gantt view).
 * Loaded via: hooks.py `doctype_list_js["Task"]`.
 *
 * @description It uses a MutationObserver to reliably detect when the Gantt chart
 * has been rendered in the DOM, and then programmatically scrolls the timeline to
 * center on the current date ('today'). This is necessary because the standard
 * Gantt view does not have a built-in option to scroll to today by default.
 *
 * NOTE: this file currently contains only the documenting header — the
 * MutationObserver/auto-scroll implementation it describes is not present in the
 * file body (it appears to have been removed or relocated). Left as-is per the
 * comments-only constraint; flagged here so the discrepancy is visible.
 */
