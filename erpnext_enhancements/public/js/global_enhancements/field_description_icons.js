/**
 * field_description_icons.js — field help text as a hover "ⓘ" info icon.
 *
 * Targets: every desk form, every doctype (global).
 * Loaded via: erpnext_enhancements.bundle.js (global desk bundle).
 * Styling: public/css/global_enhancements/field_description_icons.css
 *          (shipped via desk_addons.bundle.scss).
 *
 * Frappe renders a field's `description` inline as a `.help-box` paragraph
 * under the control. This replaces that with a small ⓘ icon next to the field
 * label; hovering (or focusing / tapping) the icon reveals the same text in a
 * floating tooltip. The inline help-box is hidden via CSS (`.ee-field-desc`),
 * not removed — so it stays the live source of the tooltip text (already
 * translated and HTML-formatted by Frappe, links intact) and we never fight
 * Frappe's per-refresh re-population of it.
 *
 * Gating: bails unless `frappe.boot.ee_field_description_icons` is truthy — the
 * `field_description_icons_enabled` switch on ERPNext Enhancements Settings,
 * shipped to the client by boot.boot_session (same live-toggle model as live
 * collab). Toggling needs no deploy; clients pick it up on next page load.
 *
 * Same family as filter_help.js; mirrors the global form-refresh hook in
 * activity_log_numbering.js.
 *
 * The longer explanation (form kit P4.3). For a DocType with a help file in the repo
 * (form_kit/help_text/, listed in `frappe.boot.sf_field_help`), the tooltip shows the
 * description (the field's short hint) and then the field's longer explanation and a
 * "Learn more" link to its section of the Knowledge Article. The help is fetched once per
 * DocType per page load, with a plain fetch() rather than frappe.call: frappe.call shows its
 * own error dialog on a 403, a 5xx or a timeout whatever `silent` says, and a tooltip must
 * never do that. A failed fetch simply leaves the tooltip as it was: the description alone.
 *
 * One opt-out: a control whose docfield carries `ee_desc_inline` is left exactly as Frappe drew
 * it (description inline under the field). The guided entry page (form kit P4-B) sets it on its
 * own FieldGroup fields; everywhere else nothing sets it, so nothing else changes.
 */
frappe.provide("erpnext_enhancements.field_description_icons");

(function () {
	if (window.__ee_field_desc_icons_loaded) return;
	window.__ee_field_desc_icons_loaded = true;

	// Inline info-circle glyph; `currentColor` lets CSS drive the color (and the
	// hover/dark-theme states) so we need no separate icon asset.
	const INFO_SVG =
		'<svg viewBox="0 0 16 16" width="13" height="13" fill="none" ' +
		'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">' +
		'<circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.4"/>' +
		'<circle cx="8" cy="4.7" r="0.95" fill="currentColor"/>' +
		'<rect x="7.2" y="6.8" width="1.6" height="5" rx="0.8" fill="currentColor"/>' +
		"</svg>";

	const PROCESSED_CLASS = "ee-field-desc"; // marks the .frappe-control wrapper
	const ICON_CLASS = "ee-field-desc-icon";

	function is_enabled() {
		return !!(frappe.boot && frappe.boot.ee_field_description_icons);
	}

	// Place the icon at the end of the field's label. Returns false if there is
	// no label to attach to (HTML / Section Break / Column Break, etc.).
	//
	// IMPORTANT: the icon is (re)inserted whenever it is missing, NOT once. Frappe's
	// set_label() does `label_span.innerHTML = __(label)` on render, which wipes any
	// icon placed inside the label — so we self-heal on icon presence rather than a
	// sticky flag, and the set_label patch below re-adds it right after that wipe.
	function add_icon(field) {
		const $wrapper = field.$wrapper;
		if (!$wrapper || !$wrapper.length) return false;

		const $control = $wrapper.is(".frappe-control")
			? $wrapper
			: $wrapper.find(".frappe-control").first();
		const $ctrl = $control.length ? $control : $wrapper;

		// Check fields label their text in `.label-area`; everything else uses
		// the standard `.control-label`.
		let $label = $ctrl.find(".control-label").first();
		if (!$label.length) $label = $ctrl.find(".checkbox .label-area").first();
		if (!$label.length) return false;

		// Marks the wrapper so CSS hides the inline help-box (persists harmlessly).
		$ctrl.addClass(PROCESSED_CLASS);

		// Icon already present in this label? nothing to do.
		if ($label.find("." + ICON_CLASS).length) return true;

		const $icon = $(
			'<span class="' +
				ICON_CLASS +
				'" role="button" tabindex="0" aria-label="' +
				frappe.utils.escape_html(__("Field help")) +
				'">' +
				INFO_SVG +
				"</span>"
		);
		// Fallback text for the rare case the help-box isn't populated yet.
		$icon.data("eeDescFallback", __(field.df.description || ""));
		// Which field it is, for the longer explanation (form kit P4.3).
		$icon.data("eeDescField", {
			doctype: field.doctype || field.df.parent || (window.cur_frm && window.cur_frm.doctype),
			fieldname: field.df.fieldname,
		});

		$label.append(" ").append($icon);
		return true;
	}

	// Decorate a single control — called right after Frappe renders its help-box
	// (see the set_description patch below). `control` has `.$wrapper` and `.df`,
	// the same shape add_icon() expects from a fields_dict entry.
	function decorate(control) {
		if (!is_enabled()) return;
		if (!control || !control.df) return;
		// The opt-out (form kit P4-B): a docfield built with `ee_desc_inline` keeps its description
		// inline, always visible under the control. Only the guided entry page sets it, on the
		// dfs it hands its FieldGroups, because there the description is the field's hint and the
		// page draws its own (i) for the longer help. No DocType field carries the key.
		if (control.df.ee_desc_inline) return;
		if (!control.df.description && !extra_for(control.doctype || control.df.parent, control.df.fieldname)) {
			return;
		}
		try {
			add_icon(control);
		} catch (e) {
			// Never let one weird field break the form.
			// eslint-disable-next-line no-console
			console.warn("field_description_icons: skipped", control.df && control.df.fieldname, e);
		}
	}

	// Decorate every described field on a form — used as a safety net and to
	// catch a form already open when the patch first lands.
	function rescan(frm) {
		frm = frm || window.cur_frm;
		if (!is_enabled() || !frm || !frm.fields_dict) return;
		Object.keys(frm.fields_dict).forEach((f) => decorate(frm.fields_dict[f]));
	}

	// --- The longer explanation (form kit P4.3) ---
	// {doctype: {article, fields: {fieldname: {help, anchor}}}} once fetched; a pending
	// fetch is held in `loading` so a form refresh never asks twice.
	const help_by_doctype = {};
	const loading = {};

	function has_help_file(doctype) {
		const list = (frappe.boot && frappe.boot.sf_field_help) || [];
		return !!doctype && list.indexOf(doctype) !== -1;
	}

	function extra_for(doctype, fieldname) {
		const data = doctype && help_by_doctype[doctype];
		const entry = data && data.fields && data.fields[fieldname];
		return entry && entry.help ? { help: entry.help, anchor: entry.anchor, article: data.article } : null;
	}

	function load_help(doctype) {
		if (!has_help_file(doctype) || help_by_doctype[doctype] || loading[doctype]) {
			return loading[doctype] || Promise.resolve();
		}
		const url =
			"/api/method/erpnext_enhancements.form_kit.api.get_field_help?doctype=" +
			encodeURIComponent(doctype);
		loading[doctype] = fetch(url, {
			credentials: "same-origin",
			headers: { Accept: "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token || "" },
		})
			.then((r) => (r.ok ? r.json() : null))
			.then((body) => {
				if (body && body.message && body.message.fields) help_by_doctype[doctype] = body.message;
			})
			.catch(() => null)
			.then(() => {
				delete loading[doctype];
			});
		return loading[doctype];
	}

	function more_html(extra) {
		if (!extra) return "";
		let html = '<p class="ee-field-desc-more">' + frappe.utils.escape_html(extra.help) + "</p>";
		if (extra.article) {
			// get_form_link gives v16's /desk/... path, which the Desk routes in place; a
			// hard-coded /app/ path would reload the whole Desk (test_desk_links).
			const href =
				frappe.utils.get_form_link("Knowledge Article", extra.article) +
				(extra.anchor ? "#" + encodeURIComponent(extra.anchor) : "");
			html +=
				'<a class="ee-field-desc-learn" href="' +
				frappe.utils.escape_html(href) +
				'">' +
				frappe.utils.escape_html(__("Learn more")) +
				"</a>";
		}
		return html;
	}

	// --- Tooltip (single body-appended element; avoids section overflow clipping) ---
	let $tip = null;
	let $active_icon = null;

	function get_tip() {
		if (!$tip) {
			$tip = $('<div class="ee-field-desc-tooltip" role="tooltip"></div>');
			$tip.on("mouseenter", () => $tip.addClass("ee-hovered"));
			$tip.on("mouseleave", () => {
				$tip.removeClass("ee-hovered");
				hide_tip();
			});
			$("body").append($tip);
		}
		return $tip;
	}

	function tip_html_for($icon) {
		// The live description element within the same control is the source of
		// truth: Frappe already translated it and rendered any HTML/links.
		// Standard controls use .help-box; Table/grid controls use .grid-description.
		const $ctrl = $icon.closest(".frappe-control");
		let $src = $ctrl.find(".help-box").first();
		if (!$src.length || !($src.html() || "").trim()) {
			$src = $ctrl.find(".grid-description").first();
		}
		const field = $icon.data("eeDescField") || {};
		const more = more_html(extra_for(field.doctype, field.fieldname));
		const html = $src.length ? ($src.html() || "").trim() : "";
		if (html) return html + more;
		const fallback = $icon.data("eeDescFallback") || "";
		return (fallback ? frappe.utils.escape_html(fallback) : "") + more;
	}

	function show_tip($icon) {
		const html = tip_html_for($icon);
		if (!html) return;
		$active_icon = $icon;
		const $t = get_tip();
		$t.html(html).addClass("ee-visible");
		position_tip($icon, $t);
	}

	function position_tip($icon, $t) {
		const el = $icon[0];
		if (!el) return;
		const r = el.getBoundingClientRect();
		// Measure after content + display are set.
		const tw = $t.outerWidth();
		const th = $t.outerHeight();
		const vw = window.innerWidth;
		const vh = window.innerHeight;
		const gap = 6;

		let top = r.bottom + gap;
		// Flip above if it would overflow the bottom.
		if (top + th > vh - 4 && r.top - gap - th > 4) {
			top = r.top - gap - th;
		}
		let left = r.left;
		// Keep within the viewport horizontally.
		if (left + tw > vw - 4) left = vw - 4 - tw;
		if (left < 4) left = 4;

		$t.css({ top: Math.round(top) + "px", left: Math.round(left) + "px" });
	}

	function hide_tip() {
		// Don't hide while the pointer is over the tooltip itself.
		if ($tip && $tip.hasClass("ee-hovered")) return;
		force_hide_tip();
	}

	function force_hide_tip() {
		if ($tip) $tip.removeClass("ee-hovered ee-visible");
		$active_icon = null;
	}

	function is_open_on(node) {
		return $active_icon && $active_icon[0] === node && $tip && $tip.hasClass("ee-visible");
	}

	// Delegated handlers — work for icons added after binding, no re-binding.
	$(document)
		.on("mouseenter focus", "." + ICON_CLASS, function () {
			show_tip($(this));
		})
		.on("mouseleave blur", "." + ICON_CLASS, function () {
			// Defer so moving the pointer onto the tooltip keeps it open.
			setTimeout(hide_tip, 60);
		})
		.on("click", "." + ICON_CLASS, function (e) {
			// Touch / no-hover affordance: toggle open/closed.
			e.preventDefault();
			e.stopPropagation();
			if (is_open_on(this)) {
				force_hide_tip();
			} else {
				show_tip($(this));
			}
		})
		.on("keydown", "." + ICON_CLASS, function (e) {
			if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
				e.preventDefault();
				show_tip($(this));
			}
		});

	// Dismiss on Escape, on scroll/resize, and on any tap outside the icon/tooltip.
	$(document).on("keydown.eeFieldDesc", (e) => {
		if (e.key === "Escape") force_hide_tip();
	});
	$(document).on("click.eeFieldDesc", (e) => {
		if (!$tip || !$tip.hasClass("ee-visible")) return;
		if ($(e.target).closest("." + ICON_CLASS + ", .ee-field-desc-tooltip").length) return;
		force_hide_tip();
	});
	// Capture-phase so scrolling an inner container (not just window) dismisses
	// the position:fixed tooltip; jQuery .on() can't register capture listeners.
	document.addEventListener("scroll", force_hide_tip, true);
	$(window).on("resize.eeFieldDesc", force_hide_tip);

	// Patch the control base so each field is decorated exactly when Frappe
	// renders it. We wrap BOTH methods, called per field inside refresh_input
	// (set_description first, then set_label):
	//   - set_description: runs when the help-box is (re)rendered.
	//   - set_label: rewrites the label's innerHTML (wiping our icon), so we
	//     re-add the icon immediately after it — this is what makes the icon
	//     stick across refreshes.
	// (The `form-refresh` event fires BEFORE refresh_fields, when neither the
	// label nor help-box exist yet — hooking it alone decorated nothing.)
	function patch_controls() {
		const ControlInput = frappe.ui && frappe.ui.form && frappe.ui.form.ControlInput;
		if (!ControlInput || ControlInput.prototype.__ee_desc_patched) return;
		["set_description", "set_label"].forEach((method) => {
			const orig = ControlInput.prototype[method];
			if (typeof orig !== "function") return;
			ControlInput.prototype[method] = function () {
				const ret = orig.apply(this, arguments);
				decorate(this);
				return ret;
			};
		});
		ControlInput.prototype.__ee_desc_patched = true;
	}

	patch_controls();
	$(document).on("app_ready", () => {
		patch_controls();
		rescan(window.cur_frm);
	});
	// Safety net: a deferred pass after the form's render chain settles
	// (setTimeout runs after run_serially's microtask chain, i.e. after
	// refresh_fields). Idempotent — add_icon no-ops on already-decorated fields.
	$(document).on("form-refresh", (e, frm) => {
		setTimeout(() => rescan(frm), 0);
		// The longer explanation: fetched once per DocType, then a rescan picks up fields
		// that have help but no description of their own.
		if (frm && has_help_file(frm.doctype) && !help_by_doctype[frm.doctype]) {
			load_help(frm.doctype).then(() => rescan(frm));
		}
	});

	erpnext_enhancements.field_description_icons.rescan = rescan;
})();
