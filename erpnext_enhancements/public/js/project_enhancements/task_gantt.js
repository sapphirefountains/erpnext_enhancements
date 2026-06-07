/**
 * @file This script enhances the standard Frappe Task list's Gantt view.
 * @description It uses a MutationObserver to reliably detect when the Gantt chart
 * has been rendered in the DOM, and then programmatically scrolls the timeline to
 * center on the current date ('today'). This is necessary because the standard
 * Gantt view does not have a built-in option to scroll to today by default.
 */
