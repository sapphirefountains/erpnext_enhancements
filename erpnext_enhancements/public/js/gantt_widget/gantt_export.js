/**
 * Gantt export — `erpnext_enhancements.gantt_export`.
 *
 * Draws a Gantt chart as standalone vector SVG from the widget's row data, and
 * turns that SVG into the four things people ask for: an .svg file, a .png
 * file, a branded print view, and (via api/gantt.py) .csv / .xlsx.
 *
 * WHY A SECOND RENDERER INSTEAD OF CAPTURING THE DHTMLX DOM
 *
 * This app shipped DOM capture once before — v1.166.0 added a PNG export built
 * on dom-to-image loaded from a CDN, and v1.167.0 removed it (commit 69fd0071).
 * Three things are wrong with that approach and all of them are structural:
 *
 *   1. DHTMLX VIRTUALISES ITS ROWS. Only the rows near the viewport exist in
 *      the DOM at any moment, so a screenshot of a 300-row chart captures the
 *      ~40 that happen to be scrolled into view. The bug does not show up on a
 *      small test project, which is exactly how it ships.
 *   2. The chart is a scrolling viewport, so a capture is clipped to whatever
 *      the user had scrolled to, at whatever zoom the screen happened to use.
 *   3. dom-to-image inlines computed styles by walking the tree; web fonts and
 *      CSS custom properties routinely come out blank or unstyled, and it was
 *      being fetched from cdnjs — a third-party runtime dependency on a page
 *      that renders customer project schedules.
 *
 * Rendering from the DATA instead makes the output deterministic and complete:
 * every loaded row is drawn regardless of scroll or virtualisation, at a
 * resolution we choose rather than the one the screen happened to have.
 *
 * DHTMLX's own `exportToPDF()` / `exportToPNG()` are deliberately NOT used —
 * they POST the entire chart to export.dhtmlx.com. Project schedules must not
 * leave the browser. Nothing in this file makes a network request except the
 * same-origin fetch of the letterhead logo.
 *
 * WHAT GETS EXPORTED: the rows currently in the datastore, in the order and
 * expansion state the user is looking at (collapsed branches stay collapsed —
 * `getTaskByIndex` walks the rendered row list, not the whole tree), minus the
 * lazy-load placeholders. So it is "what I see", just not "what fits on screen".
 *
 * Usage:
 *   const NS = erpnext_enhancements.gantt_export;
 *   await NS.export_png(widget, { title: "PRJ-0001", filename: "schedule" });
 *   await NS.export_svg(widget, { title: "PRJ-0001" });
 *   await NS.print(widget, { title: "PRJ-0001", subtitle: "Fountain rebuild" });
 */
frappe.provide("erpnext_enhancements.gantt_export");

(function () {
	const NS = erpnext_enhancements.gantt_export;
	if (NS.render_svg) {
		return; // already initialized (double bundle evaluation)
	}

	const SVG_NS = "http://www.w3.org/2000/svg";
	const XLINK_NS = "http://www.w3.org/1999/xlink";
	const DAY_MS = 86400000;

	// Layout constants, in SVG user units (= CSS px at scale 1).
	const PAD = 20; // outer padding
	const BRAND_H = 58; // header band height when branding is on
	const SCALE_H = 40; // two-band time scale header
	const ROW_H = 24;
	const BAR_H = 14;
	const BAR_R = 3; // bar corner radius
	const INDENT = 14; // px per tree level in the name column
	const FOOT_H = 22;
	const MIN_NAME_W = 150;
	const MAX_NAME_W = 340;

	// Width budgets, in SVG user units. The right ceiling depends entirely on
	// where the output is going, so the caller picks one:
	//
	//   PRINT — the page scales the whole chart to its width, so every extra
	//     pixel of timeline shrinks the text. A three-year portfolio at week
	//     zoom measures ~12,000px; flattened onto landscape Letter that is
	//     unreadable. ~5,000 is about where an 11px label survives the squeeze.
	//   IMAGE — a PNG can be zoomed into, so it can afford more. The hard stop
	//     is the canvas: Chrome refuses a bitmap over ~16,384px on a side and
	//     returns a BLANK image rather than an error, so the budget must leave
	//     room for the 2x device scale in export_png.
	//   VECTOR — an SVG has no rasterisation limit; this is only a guard against
	//     a runaway config producing a hundred-megabyte file.
	const MAX_W = {
		print: 5200,
		image: 15000,
		vector: 24000,
	};
	const MAX_TIMELINE_W = MAX_W.vector;

	const FONT =
		"Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

	// Print/export palette. Deliberately fixed and light rather than read from
	// CSS custom properties: the export must look the same for a user on the
	// dark desk theme as for one on light, and `getComputedStyle` values do not
	// survive serialisation into a standalone SVG anyway.
	const C = {
		ink: "#1a1a1a",
		muted: "#6b7280",
		faint: "#9ca3af",
		rule: "#d1d5db",
		hair: "#ececf0",
		band: "#f7f8fa",
		weekend: "#f2f4f7",
		today: "#dc2626",
		bar: "#2563eb",
		bar_done: "#1d4ed8",
		bar_group: "#475569",
		bar_late: "#dc2626",
		milestone: "#7c3aed",
		white: "#ffffff",
	};

	// Pixels per day by zoom level. These are the export's own scale — the
	// screen widget's zoom is a hint (see pick_zoom), not a constraint, because
	// what is comfortable in a scrolling viewport is often far too wide for a
	// fixed-size image or a sheet of paper.
	const PX_PER_DAY = {
		day: 26,
		week: 11,
		month: 3.6,
		quarter: 1.5,
	};

	const ZOOM_ORDER = ["day", "week", "month", "quarter"];

	// The widget's zoom presets (ZOOM_PRESETS in gantt_widget.js) are named for
	// the legacy frappe-gantt view modes and are finer-grained than anything
	// worth drawing in a static export: quarter_day and half_day differ only in
	// intra-day columns, which an exported chart has no room for. "quarter" is
	// export-only — the step-out target when even a month scale is too wide.
	const WIDGET_ZOOM_MAP = {
		quarter_day: "day",
		half_day: "day",
		day: "day",
		week: "week",
		month: "month",
	};

	/* ------------------------------------------------------------------ *
	 * Small helpers
	 * ------------------------------------------------------------------ */

	function to_date(value) {
		if (!value) {
			return null;
		}
		if (value instanceof Date) {
			return isNaN(value.getTime()) ? null : value;
		}
		// The API emits "YYYY-MM-DD HH:MM". Safari refuses that with a space,
		// so normalise to ISO-ish local time rather than trusting Date parsing.
		const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
		if (!m) {
			const d = new Date(value);
			return isNaN(d.getTime()) ? null : d;
		}
		return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
	}

	function start_of_day(d) {
		return new Date(d.getFullYear(), d.getMonth(), d.getDate());
	}

	function add_days(d, n) {
		return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
	}

	function add_months(d, n) {
		return new Date(d.getFullYear(), d.getMonth() + n, 1);
	}

	/** Whole days between two dates, ignoring DST wobble. */
	function day_span(a, b) {
		return Math.round((start_of_day(b) - start_of_day(a)) / DAY_MS);
	}

	function fmt_date(d) {
		if (!d) {
			return "";
		}
		// frappe's user date format when available, else ISO — the export is a
		// document a person reads, so it should match the rest of their desk.
		try {
			return frappe.datetime.str_to_user(frappe.datetime.obj_to_str(d));
		} catch (e) {
			return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
				d.getDate()
			).padStart(2, "0")}`;
		}
	}

	const MONTHS = [
		"Jan",
		"Feb",
		"Mar",
		"Apr",
		"May",
		"Jun",
		"Jul",
		"Aug",
		"Sep",
		"Oct",
		"Nov",
		"Dec",
	];

	/**
	 * Truncate to fit `width` px, appending an ellipsis. Uses an average glyph
	 * width rather than real text metrics: measuring would need the text in a
	 * live DOM at the export font, and being a few characters conservative is
	 * cheaper than the reflow. 0.55em is measured for this stack at 11-12px.
	 */
	function fit_text(text, width, size) {
		const s = String(text == null ? "" : text);
		const max = Math.floor(width / (size * 0.55));
		if (max <= 1) {
			return "";
		}
		return s.length <= max ? s : s.slice(0, max - 1) + "…";
	}

	function el(tag, attrs, text) {
		const node = document.createElementNS(SVG_NS, tag);
		for (const k in attrs || {}) {
			if (attrs[k] != null) {
				node.setAttribute(k, attrs[k]);
			}
		}
		if (text != null) {
			node.textContent = String(text);
		}
		return node;
	}

	/* ------------------------------------------------------------------ *
	 * Reading rows out of a live widget
	 * ------------------------------------------------------------------ */

	/**
	 * Flatten the widget's datastore into plain export rows.
	 *
	 * Reads through `getTaskByIndex` over `getVisibleTaskCount()`, which is
	 * DHTMLX's *rendered* row list — so collapsed branches are excluded and the
	 * order matches the grid exactly. Falls back to `eachTask` (whole tree) if
	 * that API is missing, which is better than exporting nothing.
	 */
	function collect_rows(widget) {
		const g = widget && widget.gantt;
		if (!g) {
			return [];
		}
		const out = [];
		const push = (task) => {
			if (!task || task.ee_placeholder) {
				return; // "Loading…" rows are chrome, not data
			}
			const start = to_date(task.start_date);
			const end = to_date(task.end_date);
			if (!start || !end) {
				return;
			}
			out.push({
				id: task.id,
				text: task.text || "",
				level: task.$level || 0,
				start: start,
				// The API's end_date is EXCLUSIVE (a date-only end is pushed
				// +1 day server-side). Keep the exclusive value for geometry
				// and derive the inclusive one for anything a human reads.
				end: end,
				end_inclusive: add_days(end, -1) >= start ? add_days(end, -1) : start,
				progress: typeof task.progress === "number" ? task.progress : null,
				type: task.type || "task",
				is_group: !!g.hasChild(task.id),
				ref_doctype: task.ref_doctype || "",
				ref_name: task.ref_name || "",
				status: task.status || "",
			});
		};

		if (typeof g.getVisibleTaskCount === "function" && typeof g.getTaskByIndex === "function") {
			const n = g.getVisibleTaskCount();
			for (let i = 0; i < n; i++) {
				push(g.getTaskByIndex(i));
			}
		}
		if (!out.length && typeof g.eachTask === "function") {
			g.eachTask((task) => push(task));
		}
		return out;
	}

	/** Overall [min start, max end) across rows, padded to whole days. */
	function date_range(rows) {
		let min = null;
		let max = null;
		rows.forEach((r) => {
			if (!min || r.start < min) {
				min = r.start;
			}
			if (!max || r.end > max) {
				max = r.end;
			}
		});
		if (!min || !max) {
			const today = start_of_day(new Date());
			return { start: today, end: add_days(today, 30) };
		}
		return { start: start_of_day(min), end: add_days(start_of_day(max), 1) };
	}

	/**
	 * Choose an export zoom. Starts from the widget's own zoom so the export
	 * resembles the screen, then steps out (day -> week -> month -> quarter)
	 * until the timeline fits `max_w`.
	 *
	 * Stepping out rather than clamping the width is the whole point: clamping
	 * would keep the scale and truncate the range, silently dropping the tail
	 * of the project off the right edge. Widening the unit keeps every task on
	 * the page and only costs precision.
	 */
	function pick_zoom(span_days, preferred, max_w) {
		max_w = max_w || MAX_TIMELINE_W;
		let i = ZOOM_ORDER.indexOf(preferred);
		if (i < 0) {
			i = 0;
		}
		while (i < ZOOM_ORDER.length - 1 && span_days * PX_PER_DAY[ZOOM_ORDER[i]] > max_w) {
			i++;
		}
		return ZOOM_ORDER[i];
	}

	/* ------------------------------------------------------------------ *
	 * Time scale
	 * ------------------------------------------------------------------ */

	/**
	 * Build the two-band header (coarse unit above, fine unit below) plus the
	 * vertical gridlines the body draws. Bands are computed as real calendar
	 * steps rather than fixed pixel intervals so month boundaries land where a
	 * reader expects them regardless of month length.
	 */
	function build_scale(range, zoom, ppd) {
		const top = [];
		const bottom = [];
		const grid = [];
		const x_of = (d) => day_span(range.start, d) * ppd;

		if (zoom === "day") {
			for (let d = new Date(range.start); d < range.end; d = add_days(d, 1)) {
				const x = x_of(d);
				bottom.push({ x: x, w: ppd, label: String(d.getDate()), dim: d.getDay() % 6 === 0 });
				grid.push({ x: x, weekend: d.getDay() % 6 === 0 });
			}
			for (let d = new Date(range.start.getFullYear(), range.start.getMonth(), 1); d < range.end; d = add_months(d, 1)) {
				const from = d < range.start ? range.start : d;
				const to = add_months(d, 1) > range.end ? range.end : add_months(d, 1);
				top.push({
					x: x_of(from),
					w: (day_span(from, to)) * ppd,
					label: `${MONTHS[d.getMonth()]} ${d.getFullYear()}`,
				});
			}
		} else if (zoom === "week") {
			// Weeks start Monday, matching the desk's calendar conventions.
			let d = new Date(range.start);
			d = add_days(d, -((d.getDay() + 6) % 7));
			for (; d < range.end; d = add_days(d, 7)) {
				const from = d < range.start ? range.start : d;
				const to = add_days(d, 7) > range.end ? range.end : add_days(d, 7);
				const x = x_of(from);
				bottom.push({ x: x, w: day_span(from, to) * ppd, label: `${d.getDate()} ${MONTHS[d.getMonth()]}` });
				grid.push({ x: x, weekend: false });
			}
			for (let m = new Date(range.start.getFullYear(), range.start.getMonth(), 1); m < range.end; m = add_months(m, 1)) {
				const from = m < range.start ? range.start : m;
				const to = add_months(m, 1) > range.end ? range.end : add_months(m, 1);
				top.push({
					x: x_of(from),
					w: day_span(from, to) * ppd,
					label: `${MONTHS[m.getMonth()]} ${m.getFullYear()}`,
				});
			}
		} else {
			// month / quarter: fine band is months, coarse band is years.
			for (let m = new Date(range.start.getFullYear(), range.start.getMonth(), 1); m < range.end; m = add_months(m, 1)) {
				const from = m < range.start ? range.start : m;
				const to = add_months(m, 1) > range.end ? range.end : add_months(m, 1);
				const x = x_of(from);
				const w = day_span(from, to) * ppd;
				bottom.push({ x: x, w: w, label: w > 26 ? MONTHS[m.getMonth()] : "" });
				grid.push({ x: x, weekend: false });
			}
			for (let y = new Date(range.start.getFullYear(), 0, 1); y < range.end; y = new Date(y.getFullYear() + 1, 0, 1)) {
				const from = y < range.start ? range.start : y;
				const to_y = new Date(y.getFullYear() + 1, 0, 1);
				const to = to_y > range.end ? range.end : to_y;
				top.push({ x: x_of(from), w: day_span(from, to) * ppd, label: String(y.getFullYear()) });
			}
		}
		return { top: top, bottom: bottom, grid: grid };
	}

	/* ------------------------------------------------------------------ *
	 * The renderer
	 * ------------------------------------------------------------------ */

	/**
	 * Render rows to an <svg> element.
	 *
	 * opts: { title, subtitle, zoom, brand: {logo, company}, columns:[...],
	 *         show_today, links }
	 * Returns { svg, width, height, zoom, truncated }.
	 */
	function render_svg(rows, opts) {
		opts = opts || {};
		const range = date_range(rows);
		const span = Math.max(1, day_span(range.start, range.end));
		const max_w = opts.max_width || MAX_TIMELINE_W;
		const requested_zoom = opts.zoom || "week";
		const zoom = pick_zoom(span, requested_zoom, max_w);
		const ppd = PX_PER_DAY[zoom];
		const timeline_w = Math.min(max_w, Math.max(240, span * ppd));
		const scale = build_scale(range, zoom, ppd);

		// Date columns are worth their width only when there is room; on a
		// narrow export the name alone is more useful than truncated dates.
		const show_dates = opts.columns !== false && rows.length > 0;
		const date_w = show_dates ? 82 : 0;
		const pct_w = show_dates ? 40 : 0;

		// Size the name column to the deepest/longest label, within bounds.
		let widest = 0;
		rows.forEach((r) => {
			widest = Math.max(widest, r.level * INDENT + String(r.text).length * 6.1);
		});
		const name_w = Math.round(Math.min(MAX_NAME_W, Math.max(MIN_NAME_W, widest + 16)));
		const grid_w = name_w + date_w * 2 + pct_w;

		const brand = opts.brand || {};
		const has_brand = !!(brand.company || brand.logo || opts.title);
		const head_h = has_brand ? BRAND_H : 0;
		const body_y = PAD + head_h + SCALE_H;
		const body_h = rows.length * ROW_H;
		const width = Math.round(PAD * 2 + grid_w + timeline_w);
		const height = Math.round(body_y + body_h + FOOT_H + PAD);

		const svg = el("svg", {
			xmlns: SVG_NS,
			"xmlns:xlink": XLINK_NS,
			width: width,
			height: height,
			viewBox: `0 0 ${width} ${height}`,
			"font-family": FONT,
		});

		// Opaque background: a transparent PNG pasted into a dark slide deck
		// renders the (dark) text invisible.
		svg.appendChild(el("rect", { x: 0, y: 0, width: width, height: height, fill: C.white }));

		/* ---- brand band ---- */
		if (has_brand) {
			const g = el("g", {});
			let tx = PAD;
			if (brand.logo) {
				const img = el("image", { x: PAD, y: PAD - 2, height: 34, preserveAspectRatio: "xMinYMid meet" });
				// href for SVG2, xlink:href for the rasteriser in older engines.
				img.setAttributeNS(XLINK_NS, "xlink:href", brand.logo);
				img.setAttribute("href", brand.logo);
				g.appendChild(img);
				tx = PAD + (brand.logo_width || 120) + 14;
			}
			if (opts.title) {
				g.appendChild(
					el("text", { x: tx, y: PAD + 15, "font-size": 15, "font-weight": 700, fill: C.ink }, opts.title)
				);
			}
			if (opts.subtitle) {
				g.appendChild(
					el("text", { x: tx, y: PAD + 31, "font-size": 11, fill: C.muted }, opts.subtitle)
				);
			}
			const meta = `${fmt_date(range.start)} – ${fmt_date(add_days(range.end, -1))}  ·  ${
				rows.length
			} ${rows.length === 1 ? "row" : "rows"}`;
			g.appendChild(
				el("text", { x: width - PAD, y: PAD + 15, "font-size": 11, fill: C.muted, "text-anchor": "end" }, meta)
			);
			if (brand.company) {
				g.appendChild(
					el(
						"text",
						{ x: width - PAD, y: PAD + 31, "font-size": 11, fill: C.faint, "text-anchor": "end" },
						brand.company
					)
				);
			}
			g.appendChild(
				el("line", {
					x1: PAD,
					y1: PAD + head_h - 10,
					x2: width - PAD,
					y2: PAD + head_h - 10,
					stroke: C.ink,
					"stroke-width": 1.5,
				})
			);
			svg.appendChild(g);
		}

		const gx = PAD; // grid (name column) origin
		const tx0 = PAD + grid_w; // timeline origin
		const head_y = PAD + head_h;

		/* ---- scale header ---- */
		const head = el("g", {});
		head.appendChild(
			el("rect", { x: gx, y: head_y, width: grid_w + timeline_w, height: SCALE_H, fill: C.band })
		);
		scale.top.forEach((b) => {
			if (b.w < 2) {
				return;
			}
			head.appendChild(
				el("line", { x1: tx0 + b.x, y1: head_y, x2: tx0 + b.x, y2: head_y + SCALE_H, stroke: C.rule })
			);
			const label = fit_text(b.label, b.w - 6, 10);
			if (label) {
				head.appendChild(
					el(
						"text",
						{ x: tx0 + b.x + 5, y: head_y + 13, "font-size": 10, "font-weight": 600, fill: C.ink },
						label
					)
				);
			}
		});
		head.appendChild(
			el("line", {
				x1: tx0,
				y1: head_y + SCALE_H / 2,
				x2: tx0 + timeline_w,
				y2: head_y + SCALE_H / 2,
				stroke: C.rule,
			})
		);
		scale.bottom.forEach((b) => {
			if (b.w < 1) {
				return;
			}
			if (b.dim) {
				head.appendChild(
					el("rect", {
						x: tx0 + b.x,
						y: head_y + SCALE_H / 2,
						width: b.w,
						height: SCALE_H / 2,
						fill: C.weekend,
					})
				);
			}
			const label = fit_text(b.label, b.w - 2, 9);
			if (label) {
				head.appendChild(
					el(
						"text",
						{
							x: tx0 + b.x + b.w / 2,
							y: head_y + SCALE_H - 7,
							"font-size": 9,
							fill: C.muted,
							"text-anchor": "middle",
						},
						label
					)
				);
			}
		});

		// Grid-column headings
		const cols = [{ x: gx + 6, label: __("Task"), anchor: "start" }];
		if (show_dates) {
			cols.push({ x: gx + name_w + date_w - 6, label: __("Start"), anchor: "end" });
			cols.push({ x: gx + name_w + date_w * 2 - 6, label: __("End"), anchor: "end" });
			cols.push({ x: gx + name_w + date_w * 2 + pct_w - 6, label: "%", anchor: "end" });
		}
		cols.forEach((c) => {
			head.appendChild(
				el(
					"text",
					{
						x: c.x,
						y: head_y + SCALE_H - 12,
						"font-size": 10,
						"font-weight": 600,
						fill: C.muted,
						"text-anchor": c.anchor,
					},
					c.label
				)
			);
		});
		svg.appendChild(head);

		/* ---- timeline gridlines + weekend shading ---- */
		const grid_g = el("g", {});
		scale.grid.forEach((line) => {
			if (line.weekend && ppd >= 6) {
				grid_g.appendChild(
					el("rect", { x: tx0 + line.x, y: body_y, width: ppd, height: body_h, fill: C.weekend })
				);
			}
			if (ppd >= 6 || !line.weekend) {
				grid_g.appendChild(
					el("line", {
						x1: tx0 + line.x,
						y1: body_y,
						x2: tx0 + line.x,
						y2: body_y + body_h,
						stroke: C.hair,
					})
				);
			}
		});
		svg.appendChild(grid_g);

		/* ---- rows ---- */
		const body = el("g", {});
		rows.forEach((r, i) => {
			const y = body_y + i * ROW_H;
			if (i % 2 === 1) {
				body.appendChild(
					el("rect", { x: gx, y: y, width: grid_w + timeline_w, height: ROW_H, fill: C.band, opacity: 0.6 })
				);
			}
			body.appendChild(
				el("line", { x1: gx, y1: y + ROW_H, x2: gx + grid_w + timeline_w, y2: y + ROW_H, stroke: C.hair })
			);

			// name cell
			const indent = r.level * INDENT;
			const label = fit_text(r.text, name_w - indent - 12, 11);
			body.appendChild(
				el(
					"text",
					{
						x: gx + 6 + indent,
						y: y + ROW_H / 2 + 4,
						"font-size": 11,
						"font-weight": r.is_group ? 600 : 400,
						fill: r.is_group ? C.ink : "#333",
					},
					label
				)
			);

			if (show_dates) {
				const cells = [
					{ x: gx + name_w + date_w - 6, v: fmt_date(r.start) },
					{ x: gx + name_w + date_w * 2 - 6, v: fmt_date(r.end_inclusive) },
					{
						x: gx + name_w + date_w * 2 + pct_w - 6,
						v: r.progress == null ? "" : Math.round(r.progress * 100) + "%",
					},
				];
				cells.forEach((c) => {
					body.appendChild(
						el(
							"text",
							{ x: c.x, y: y + ROW_H / 2 + 4, "font-size": 10, fill: C.muted, "text-anchor": "end" },
							c.v
						)
					);
				});
			}

			// bar
			const x1 = tx0 + day_span(range.start, r.start) * ppd;
			const x2 = tx0 + day_span(range.start, r.end) * ppd;
			const w = Math.max(2, x2 - x1);
			const by = y + (ROW_H - BAR_H) / 2;

			if (r.type === "milestone" || (w <= 3 && r.progress === null)) {
				const cy = y + ROW_H / 2;
				const s = 6;
				body.appendChild(
					el("path", {
						d: `M ${x1} ${cy - s} L ${x1 + s} ${cy} L ${x1} ${cy + s} L ${x1 - s} ${cy} Z`,
						fill: C.milestone,
					})
				);
			} else if (r.type === "project" || r.is_group) {
				// Summary bar: a slim capped rule, so a parent never looks like
				// scheduled work of its own.
				body.appendChild(
					el("rect", { x: x1, y: by + 3, width: w, height: 6, fill: C.bar_group, rx: 2 })
				);
				body.appendChild(el("rect", { x: x1, y: by + 3, width: 2, height: 11, fill: C.bar_group }));
				body.appendChild(el("rect", { x: x2 - 2, y: by + 3, width: 2, height: 11, fill: C.bar_group }));
			} else {
				const overdue = r.end < new Date() && (r.progress == null || r.progress < 1);
				body.appendChild(
					el("rect", {
						x: x1,
						y: by,
						width: w,
						height: BAR_H,
						rx: BAR_R,
						fill: overdue ? C.bar_late : C.bar,
						"fill-opacity": 0.28,
						stroke: overdue ? C.bar_late : C.bar,
						"stroke-width": 1,
					})
				);
				if (r.progress) {
					body.appendChild(
						el("rect", {
							x: x1,
							y: by,
							width: Math.max(1, w * Math.min(1, r.progress)),
							height: BAR_H,
							rx: BAR_R,
							fill: overdue ? C.bar_late : C.bar_done,
						})
					);
				}
				// Label a bar only when it is wide enough to hold text.
				if (w > 46) {
					const t = fit_text(r.text, w - 10, 9);
					if (t) {
						body.appendChild(
							el(
								"text",
								{
									x: x1 + 5,
									y: by + BAR_H - 4,
									"font-size": 9,
									fill: r.progress > 0.55 ? C.white : C.ink,
								},
								t
							)
						);
					}
				}
			}
		});
		svg.appendChild(body);

		/* ---- frame + column rules (drawn last, over the row fills) ---- */
		const frame = el("g", {});
		const verticals = [gx, gx + name_w];
		if (show_dates) {
			verticals.push(gx + name_w + date_w, gx + name_w + date_w * 2, gx + grid_w);
		} else {
			verticals.push(gx + grid_w);
		}
		verticals.forEach((x) => {
			frame.appendChild(
				el("line", { x1: x, y1: head_y, x2: x, y2: body_y + body_h, stroke: C.rule })
			);
		});
		frame.appendChild(
			el("rect", {
				x: gx,
				y: head_y,
				width: grid_w + timeline_w,
				height: SCALE_H + body_h,
				fill: "none",
				stroke: C.rule,
			})
		);
		frame.appendChild(
			el("line", { x1: gx, y1: body_y, x2: gx + grid_w + timeline_w, y2: body_y, stroke: C.rule })
		);

		/* ---- today ---- */
		if (opts.show_today !== false) {
			const today = start_of_day(new Date());
			if (today >= range.start && today < range.end) {
				const x = tx0 + day_span(range.start, today) * ppd;
				frame.appendChild(
					el("line", {
						x1: x,
						y1: head_y + SCALE_H / 2,
						x2: x,
						y2: body_y + body_h,
						stroke: C.today,
						"stroke-width": 1.5,
					})
				);
				frame.appendChild(
					el("path", { d: `M ${x - 4} ${head_y + SCALE_H / 2} L ${x + 4} ${head_y + SCALE_H / 2} L ${x} ${head_y + SCALE_H / 2 + 5} Z`, fill: C.today })
				);
			}
		}
		svg.appendChild(frame);

		/* ---- footer ---- */
		svg.appendChild(
			el(
				"text",
				{ x: gx, y: height - PAD + 2, "font-size": 9, fill: C.faint },
				__("Generated {0}", [fmt_date(new Date())])
			)
		);
		// Say so when the export is coarser than what is on screen — otherwise a
		// reader compares the file against the chart, sees different columns,
		// and reasonably concludes the export is wrong.
		if (zoom !== requested_zoom) {
			svg.appendChild(
				el(
					"text",
					{ x: width - PAD, y: height - PAD + 2, "font-size": 9, fill: C.faint, "text-anchor": "end" },
					__("Scaled to {0} view to fit ({1} days)", [zoom, span])
				)
			);
		}

		return { svg: svg, width: width, height: height, zoom: zoom, rows: rows.length };
	}

	/* ------------------------------------------------------------------ *
	 * Serialisation / rasterisation / download
	 * ------------------------------------------------------------------ */

	function svg_string(svg) {
		const s = new XMLSerializer().serializeToString(svg);
		return '<?xml version="1.0" encoding="UTF-8"?>\n' + s;
	}

	/**
	 * Rasterise an <svg> to a PNG Blob at `scale`x.
	 *
	 * Goes through a blob: URL rather than a data: URI — a base64 data URI of a
	 * multi-megabyte SVG blows the call stack in `String.fromCharCode(...)` and
	 * is slower to parse. blob: is same-origin, so the canvas is NOT tainted
	 * and toBlob() works; that is only true because nothing in the SVG points
	 * at a remote host (the logo is inlined as a data URI before we get here).
	 */
	function svg_to_png(svg, scale) {
		scale = scale || 2;
		const width = +svg.getAttribute("width");
		const height = +svg.getAttribute("height");
		const str = svg_string(svg);
		const blob = new Blob([str], { type: "image/svg+xml;charset=utf-8" });
		const url = URL.createObjectURL(blob);

		return new Promise((resolve, reject) => {
			const img = new Image();
			img.onload = () => {
				try {
					const canvas = document.createElement("canvas");
					canvas.width = Math.round(width * scale);
					canvas.height = Math.round(height * scale);
					const ctx = canvas.getContext("2d");
					ctx.fillStyle = C.white;
					ctx.fillRect(0, 0, canvas.width, canvas.height);
					ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
					canvas.toBlob((out) => {
						URL.revokeObjectURL(url);
						out ? resolve(out) : reject(new Error("canvas.toBlob returned null"));
					}, "image/png");
				} catch (e) {
					URL.revokeObjectURL(url);
					reject(e);
				}
			};
			img.onerror = () => {
				URL.revokeObjectURL(url);
				reject(new Error("SVG could not be rasterised"));
			};
			img.src = url;
		});
	}

	// Branding, downloads and the print shell are shared with the Scope-tab
	// task tree — see public/js/export_utils.js.
	const U = () => erpnext_enhancements.export_utils;

	/* ------------------------------------------------------------------ *
	 * Public entry points
	 * ------------------------------------------------------------------ */

	async function build(widget, opts) {
		opts = opts || {};
		const rows = collect_rows(widget);
		if (!rows.length) {
			frappe.show_alert({ message: __("Nothing to export."), indicator: "orange" });
			return null;
		}
		const brand = await U().build_brand(opts);
		const widget_zoom = widget && widget.config && widget.config.zoom;
		return render_svg(rows, {
			title: opts.title || "",
			subtitle: opts.subtitle || "",
			zoom: opts.zoom || WIDGET_ZOOM_MAP[widget_zoom] || "week",
			brand: brand,
			columns: opts.columns,
			show_today: opts.show_today,
			max_width: opts.max_width || MAX_W.vector,
		});
	}

	function out_filename(opts, extension) {
		const u = U();
		return `${u.safe_filename((opts && opts.filename) || (opts && opts.title), "gantt")}-${u.stamp()}.${extension}`;
	}

	async function export_svg(widget, opts) {
		const out = await build(widget, opts);
		if (!out) {
			return;
		}
		const blob = new Blob([svg_string(out.svg)], { type: "image/svg+xml;charset=utf-8" });
		U().download_blob(blob, out_filename(opts, "svg"));
	}

	async function export_png(widget, opts) {
		const out = await build(widget, { ...opts, max_width: MAX_W.image });
		if (!out) {
			return;
		}
		// 2x keeps text crisp when the image is dropped into a slide or a PDF.
		// Wide charts drop to 1x so the canvas stays inside browser limits:
		// Chrome refuses a bitmap over ~16,384px on a side and hands back a
		// BLANK image rather than throwing, so this must be conservative.
		const scale = out.width > 7500 ? 1 : 2;
		const blob = await svg_to_png(out.svg, scale);
		U().download_blob(blob, out_filename(opts, "png"));
	}

	/**
	 * Open the chart in a print window.
	 *
	 * The SVG is rendered WITHOUT its own header band here — the print document
	 * supplies one (export_utils.print_document), and two logos stacked on one
	 * page looks like a bug. Landscape by default because a Gantt is always
	 * wider than it is tall.
	 *
	 * Fitting: width:100% + height:auto on the root <svg>, with the viewBox
	 * left intact, makes the browser scale the whole chart to the page width.
	 * That is the only mechanism here that copes with a chart far wider than
	 * the paper, and it costs legibility on very wide ranges — which is why
	 * pick_zoom steps the scale out rather than letting width grow unbounded.
	 */
	async function print_chart(widget, opts) {
		opts = opts || {};
		const out = await build(widget, {
			...opts,
			brand: false, // the print header carries the branding instead
			title: "",
			subtitle: "",
			max_width: MAX_W.print,
		});
		if (!out) {
			return;
		}
		const svg = out.svg;
		svg.setAttribute("width", "100%");
		svg.setAttribute("height", "auto");
		svg.setAttribute("style", "max-width:100%;height:auto;");
		U().print_document(svg_string(svg), {
			title: opts.title || __("Gantt Chart"),
			subtitle: opts.subtitle || "",
			brand: await U().build_brand({}),
			orientation: opts.orientation || "landscape",
			margin: "10mm",
			footer: __("{0} rows · {1} view", [out.rows, out.zoom]),
		});
	}

	NS.collect_rows = collect_rows;
	NS.render_svg = render_svg;
	NS.svg_string = svg_string;
	NS.svg_to_png = svg_to_png;
	NS.date_range = date_range;
	NS.build = build;
	NS.export_svg = export_svg;
	NS.export_png = export_png;
	NS.print = print_chart;
})();
