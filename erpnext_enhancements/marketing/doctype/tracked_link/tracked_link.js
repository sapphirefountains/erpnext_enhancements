// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Tracked Link (F5, v1.757.0): the tagged link, its QR code to print, and what came of it. The tagged
// address and the SVG are built on the server on save (marketing/performance/links.py); this draws them.

// The SVG as a data: URL for an <img>, which never runs script.
function tracked_link_qr_src(svg) {
	return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg || "");
}

frappe.ui.form.on("Tracked Link", {
	refresh(frm) {
		const wrapper = frm.fields_dict.qr_html && frm.fields_dict.qr_html.$wrapper;
		if (wrapper) {
			if (frm.doc.qr_svg && frm.doc.tagged_url) {
				// Drawn as an image, never as markup: an <img> runs no script, whatever the field holds. The
				// server also rebuilds the SVG on every save (marketing/performance/links.py).
				wrapper.html(
					`<img class="tracked-link-qr" style="max-width:220px" alt="${frappe.utils.escape_html(__("QR code"))}" src="${tracked_link_qr_src(frm.doc.qr_svg)}">
					<p class="text-muted small">${frappe.utils.escape_html(frm.doc.tagged_url)}</p>`
				);
			} else {
				wrapper.html(`<p class="text-muted">${__("Save to make the tagged link and its QR code.")}</p>`);
			}
		}
		if (frm.is_new()) return;
		frm.add_custom_button(__("Copy link"), () => frappe.utils.copy_to_clipboard(frm.doc.tagged_url || ""));
		frm.add_custom_button(__("Print QR code"), () => {
			const win = window.open("", "_blank");
			if (!win) return;
			win.document.write(
				`<html><head><title>${frappe.utils.escape_html(frm.doc.title || "")}</title></head><body style="text-align:center;font-family:sans-serif"><img style="width:320px" src="${tracked_link_qr_src(
					frm.doc.qr_svg || ""
				)}"><p>${frappe.utils.escape_html(frm.doc.title || "")}</p></body></html>`
			);
			win.document.close();
			win.focus();
			win.print();
		});
		frm.add_custom_button(__("Results"), () =>
			frappe.call({
				method: "erpnext_enhancements.marketing.performance.links.get_link_results",
				args: { name: frm.doc.name },
				type: "POST",
				callback: (r) => {
					const m = (r && r.message) || {};
					frappe.msgprint({
						title: __("{0} to {1}", [m.from_date, m.to_date]),
						message: __("{0} sessions ({1} engaged, {2} key events) counted by GA4 under these tags, and {3} inquiries carrying them.", [
							m.sessions || 0,
							m.engaged_sessions || 0,
							m.key_events || 0,
							m.inquiries || 0,
						]),
					});
				},
			})
		);
	},
});
