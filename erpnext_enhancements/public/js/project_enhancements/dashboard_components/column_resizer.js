/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Reusable drag-to-resize column widths for the dashboard tables and the task grid.
 *
 * Sibling of {@link ColumnSelector} (which hides/shows columns): this component lets
 * a user drag a column's right edge to widen/narrow it, and persists the chosen
 * pixel widths **per user** in localStorage under `storageKey`. It is DOM-agnostic —
 * the two call sites differ (real `<table>` vs. a flexbox grid), so the caller
 * supplies three small handlers:
 *
 *   - `applyWidth(root, key, px)` — pin column `key` to `px` px within `root`
 *     (pass `px === null` to clear the override and return to the default).
 *   - `measureWidth(root, key)`  — the column's current rendered width in px, used
 *     as the drag's starting point the first time a column is resized.
 *   - `afterApply(root)`         — optional; run once after a batch of applyWidths
 *     (e.g. to re-derive a table's total width).
 *
 * Loaded via: the global `erpnext_enhancements.bundle.js` (so the class is always
 * defined on the desk) and additionally `frappe.require`d by the Projects Dashboard
 * Custom HTML Block, which runs in its own sandbox and can't assume bundle order.
 */
erpnext_enhancements.dashboard_components.ColumnResizer = class ColumnResizer {
	/**
	 * @param {string} storageKey localStorage key used to persist column widths.
	 * @param {Array<{key: string, defaultWidth?: number, minWidth?: number, maxWidth?: number}>} columns
	 *   Resizable column definitions. `defaultWidth` (when given) is applied even
	 *   before the user resizes anything — required for `<table>` fixed layout so
	 *   the baseline look is preserved; omit it for the flex grid, whose CSS already
	 *   defines defaults.
	 * @param {{applyWidth: Function, measureWidth: Function, afterApply?: Function}} handlers
	 */
	constructor(storageKey, columns, handlers) {
		this.storageKey = storageKey;
		this.columns = columns || [];
		this.handlers = handlers || {};
		this.widths = this._load();
		this._colByKey = {};
		this.columns.forEach((c) => (this._colByKey[c.key] = c));
		this._pending = null;
	}

	_load() {
		try {
			const raw = localStorage.getItem(this.storageKey);
			const parsed = raw ? JSON.parse(raw) : {};
			return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
		} catch (e) {
			return {};
		}
	}

	_save() {
		try {
			localStorage.setItem(this.storageKey, JSON.stringify(this.widths));
		} catch (e) {
			// Ignore storage errors (e.g. private browsing mode)
		}
	}

	_clamp(col, px) {
		const min = col && col.minWidth ? col.minWidth : 60;
		const max = col && col.maxWidth ? col.maxWidth : 800;
		return Math.max(min, Math.min(max, Math.round(px)));
	}

	/** Effective width for a column: user override, else its default, else null. */
	width_for(key) {
		if (this.widths[key] != null) return this.widths[key];
		const col = this._colByKey[key];
		if (col && col.defaultWidth != null) return col.defaultWidth;
		return null;
	}

	/** Applies every column's effective width (or clears it) within `root`. */
	apply(root) {
		if (typeof this.handlers.applyWidth !== "function") return;
		this.columns.forEach((col) => {
			this.handlers.applyWidth(root, col.key, this.width_for(col.key));
		});
		if (typeof this.handlers.afterApply === "function") this.handlers.afterApply(root);
	}

	/** Clears all user overrides and re-applies (defaults for tables, none for the grid). */
	reset(root) {
		this.widths = {};
		this._save();
		this.apply(root);
	}

	_inject_styles() {
		if (document.getElementById("ee-column-resizer-styles")) return;
		// The handle sits just INSIDE the cell's right edge (right: 0) rather than
		// straddling it, so it survives the cells' `overflow: hidden` instead of
		// being clipped away.
		$("<style id='ee-column-resizer-styles'>").html(`
			.ee-col-resize-handle {
				position: absolute; top: 0; right: 0; width: 8px; height: 100%;
				cursor: col-resize; z-index: 5; touch-action: none;
			}
			.ee-col-resize-handle::after {
				content: ""; position: absolute; top: 15%; right: 2px;
				width: 2px; height: 70%; border-radius: 2px;
				background: var(--border-color); opacity: 0; transition: opacity 0.15s;
			}
			.ee-col-resize-handle:hover::after { opacity: 1; background: var(--primary); }
			body.ee-col-resizing, body.ee-col-resizing * {
				cursor: col-resize !important; user-select: none !important;
			}
		`).appendTo("head");
	}

	/**
	 * Adds a drag handle to each header cell in `headerCells` whose column is
	 * resizable. Idempotent per cell. `keyOf($cell)` returns the column key.
	 * @param {JQuery} root Container passed back to the handlers.
	 * @param {JQuery} headerCells Header cells (`<th>` or `.task-grid-cell`).
	 * @param {(cell: JQuery) => string} keyOf Maps a header cell to its column key.
	 */
	attach_handles(root, headerCells, keyOf) {
		this._inject_styles();
		const me = this;
		headerCells.each(function () {
			const $cell = $(this);
			const key = keyOf($cell);
			if (!key || !me._colByKey[key]) return;
			if ($cell.children(".ee-col-resize-handle").length) return;

			const $handle = $('<div class="ee-col-resize-handle" title="Drag to resize"></div>');
			$cell.css("position", "relative").append($handle);

			// Swallow clicks so a resize doesn't also trigger the header's sort.
			$handle.on("click", (e) => {
				e.stopPropagation();
				e.preventDefault();
			});

			$handle.on("mousedown touchstart", (e) => {
				e.stopPropagation();
				e.preventDefault();
				const col = me._colByKey[key];
				const oe = e.originalEvent || e;
				const startX = oe.touches ? oe.touches[0].clientX : oe.clientX;
				let startW = me.width_for(key);
				if (startW == null && typeof me.handlers.measureWidth === "function") {
					startW = me.handlers.measureWidth(root, key);
				}
				startW = startW || 120;
				$("body").addClass("ee-col-resizing");

				const onMove = (ev) => {
					if (ev.cancelable) ev.preventDefault();
					const cx = ev.touches ? ev.touches[0].clientX : ev.clientX;
					const px = me._clamp(col, startW + (cx - startX));
					me._pending = px;
					if (typeof me.handlers.applyWidth === "function") {
						me.handlers.applyWidth(root, key, px);
					}
					if (typeof me.handlers.afterApply === "function") {
						me.handlers.afterApply(root);
					}
				};
				const onUp = () => {
					document.removeEventListener("mousemove", onMove);
					document.removeEventListener("mouseup", onUp);
					document.removeEventListener("touchmove", onMove);
					document.removeEventListener("touchend", onUp);
					$("body").removeClass("ee-col-resizing");
					if (me._pending != null) {
						me.widths[key] = me._pending;
						me._save();
						me._pending = null;
					}
				};
				document.addEventListener("mousemove", onMove);
				document.addEventListener("mouseup", onUp);
				document.addEventListener("touchmove", onMove, { passive: false });
				document.addEventListener("touchend", onUp);
			});
		});
	}
};
