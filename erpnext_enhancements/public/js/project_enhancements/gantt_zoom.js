/**
 * @file Shared zoom ladder for every Frappe Gantt chart in this app
 * (the Project form "Schedule" tab and the Project Dashboard portfolio Gantt).
 *
 * Targets: shared helper (no DOM of its own); consumed by the Project form Gantt
 * (project.js).
 * Loaded via: hooks.py `app_include_js` (global), so
 * `erpnext_enhancements.gantt_zoom` is always available before any Gantt renders.
 *
 * @description The +/- zoom buttons step through a single ordered ladder of
 * (view_mode, column_width) pairs. The ladder runs from most zoomed-OUT (widest
 * time span, least detail) to most zoomed-IN, and pixels-per-day rises
 * monotonically across it — so even though zooming sometimes crosses a view-mode
 * boundary (e.g. Week -> Day), it always feels like one continuous zoom.
 *
 * Frappe Gantt resolves a bar's column width as
 *   options.column_width || view_mode.column_width || 45
 * so once we set an explicit options.column_width (which we always do here), it
 * wins over the view mode's built-in width. That is exactly what lets a single
 * view mode host several zoom levels.
 */
frappe.provide("erpnext_enhancements.gantt_zoom");

(function () {
	const ns = erpnext_enhancements.gantt_zoom;

	// Ordered from most zoomed-OUT to most zoomed-IN. The trailing comment on
	// each line is the resulting pixels-per-day, which must stay monotonic.
	ns.LEVELS = [
		{ view_mode: "Month", column_width: 90 }, // ~3 px/day
		{ view_mode: "Month", column_width: 120 }, // ~4 px/day
		{ view_mode: "Week", column_width: 70 }, // ~10 px/day
		{ view_mode: "Week", column_width: 140 }, // ~20 px/day
		{ view_mode: "Day", column_width: 30 }, // 30 px/day
		{ view_mode: "Day", column_width: 45 }, // 45 px/day
		{ view_mode: "Day", column_width: 70 }, // 70 px/day
		{ view_mode: "Half Day", column_width: 45 }, // ~90 px/day
		{ view_mode: "Quarter Day", column_width: 45 }, // ~180 px/day
	];

	// Per-chart default ladder index, picked so first load looks exactly like it
	// did before zoom existed (Project form = Day/45, dashboard = Month/120).
	ns.DEFAULT_INDEX = { Day: 5, Month: 1 };

	// First ladder index for each view mode. Used to keep the +/- zoom level
	// coherent when a user clicks a discrete view-mode button (Project form).
	ns.BASE_INDEX = { Month: 1, Week: 3, Day: 5, "Half Day": 7, "Quarter Day": 8 };

	ns.clamp = function (i) {
		return Math.max(0, Math.min(ns.LEVELS.length - 1, i));
	};

	ns.level = function (i) {
		return ns.LEVELS[ns.clamp(i)];
	};

	/**
	 * Apply a zoom index to a live Gantt instance. update_options() re-renders
	 * while maintaining the current horizontal scroll position, so zooming never
	 * snaps the viewport back to today.
	 * @param {Gantt} gantt - A Frappe Gantt instance.
	 * @param {number} i - Target ladder index (clamped to range).
	 * @returns {number} The clamped index that was applied.
	 */
	ns.apply = function (gantt, i) {
		const lvl = ns.level(i);
		gantt.update_options({ view_mode: lvl.view_mode, column_width: lvl.column_width });
		return ns.clamp(i);
	};

	ns.is_min = function (i) {
		return ns.clamp(i) <= 0;
	};

	ns.is_max = function (i) {
		return ns.clamp(i) >= ns.LEVELS.length - 1;
	};
})();
