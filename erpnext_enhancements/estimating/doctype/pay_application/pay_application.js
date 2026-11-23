// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * The Pay Application form (estimating milestone 2). The lines editor is pay_app_editor.bundle.js
 * (public/js/estimating/pay_app_editor.js); this script loads it and mounts it into `lines_editor`. The
 * table view under it edits the same rows. Certifying is the standard Submit; the server refuses with every
 * reason at once (pay_application.py, pay_app_math.refusals).
 */

// The editor bundle's API level this script needs; equal to VERSION in public/js/pay_app_editor.bundle.js
// (scripts/test_estimate_math.mjs fails the build if they differ). A tab open across a deploy can pair this
// script with an older bundle, so it asks first and draws a "reload" note rather than throwing.
const REQUIRED_PAY_EDITOR_LEVEL = 2;

function with_pay_editor(frm, fn) {
	frappe.require(["pay_app_editor.bundle.js", "estimate_editor.bundle.css", "pay_app_editor.bundle.css"], () => {
		const ed = window.sf_pay_app_editor;
		if (!ed || !(ed.version >= REQUIRED_PAY_EDITOR_LEVEL)) {
			const wrap = frm.fields_dict.lines_editor && frm.fields_dict.lines_editor.$wrapper;
			if (wrap) {
				wrap.html(
					`<div class="alert alert-warning">${__("The pay application editor was updated. Reload this page to use it.")}</div>`
				);
			}
			return;
		}
		fn(ed);
	});
}

frappe.ui.form.on("Pay Application", {
	refresh(frm) {
		with_pay_editor(frm, (ed) => ed.mount(frm));
		if (frm.doc.schedule_of_values) {
			frm.add_custom_button(
				__("Schedule of Values"),
				() => frappe.set_route("Form", "Schedule of Values", frm.doc.schedule_of_values),
				__("View")
			);
		}
		if (frm.doc.previous_application) {
			frm.add_custom_button(
				__("Previous Application"),
				() => frappe.set_route("Form", "Pay Application", frm.doc.previous_application),
				__("View")
			);
		}
	},
	milestone: (frm) => frm.doc.docstatus === 0 && frm.save(),
	// B9: the type changes what the server prices (a Final application releases all retainage).
	application_type: (frm) => frm.doc.docstatus === 0 && !frm.is_new() && frm.save(),
	work_retainage_override: (frm) => !frm.__pay_editing && with_pay_editor(frm, (ed) => ed.numbers(frm)),
	stored_retainage_override: (frm) => !frm.__pay_editing && with_pay_editor(frm, (ed) => ed.numbers(frm)),
});

// A row edited in the table view moves the editor's numbers; skipped while the editor is the one writing.
const numbers = (frm) => !frm.__pay_editing && with_pay_editor(frm, (ed) => ed.numbers(frm));
const line_handlers = {};
["this_period_pct", "stored", "work_retainage_override", "stored_retainage_override", "retainage_release"].forEach((f) => (line_handlers[f] = numbers));
frappe.ui.form.on("Pay Application Line", line_handlers);
