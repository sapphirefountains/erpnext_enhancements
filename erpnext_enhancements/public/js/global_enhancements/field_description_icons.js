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
	function add_icon(field) {
		const $wrapper = field.$wrapper;
		if (!$wrapper || !$wrapper.length) return false;

		const $control = $wrapper.is(".frappe-control")
			? $wrapper
			: $wrapper.find(".frappe-control").first();
		const $ctrl = $control.length ? $control : $wrapper;

		if ($ctrl.hasClass(PROCESSED_CLASS)) return true; // already done

		// Check fields label their text in `.label-area`; everything else uses
		// the standard `.control-label`.
		let $label = $ctrl.find(".control-label").first();
		if (!$label.length) $label = $ctrl.find(".checkbox .label-area").first();
		if (!$label.length) return false;
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
		$icon.data("eeDescFallback", __(field.df.description));

		$label.append(" ").append($icon);
		$ctrl.addClass(PROCESSED_CLASS);
		return true;
	}

	function enhance(frm) {
		if (!is_enabled() || !frm || !frm.fields_dict) return;
		Object.keys(frm.fields_dict).forEach((fieldname) => {
			const field = frm.fields_dict[fieldname];
			if (!field || !field.df || !field.df.description) return;
			try {
				add_icon(field);
			} catch (e) {
				// Never let one weird field break the whole pass.
				// eslint-disable-next-line no-console
				console.warn("field_description_icons: skipped", fieldname, e);
			}
		});
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
		// The live help-box (sibling within the same control) is the source of
		// truth: Frappe already translated it and rendered any HTML/links.
		const $help = $icon.closest(".frappe-control").find(".help-box").first();
		const html = $help.length ? ($help.html() || "").trim() : "";
		if (html) return html;
		const fallback = $icon.data("eeDescFallback") || "";
		return frappe.utils.escape_html(fallback);
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

	// Run on every form refresh (fires per doctype with frm — see
	// activity_log_numbering.js for the same pattern).
	$(document).on("form-refresh", (e, frm) => enhance(frm));

	erpnext_enhancements.field_description_icons.enhance = enhance;
})();
