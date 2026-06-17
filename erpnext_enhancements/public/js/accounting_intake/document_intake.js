// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Review form for the Accounting Document Intake queue: renders the scanned
// document, surfaces extraction confidence, and exposes the role-gated review
// actions (inventory-clerk Item approval; accountant Approve/Reject/Re-extract).
frappe.ui.form.on("Document Intake", {
	refresh(frm) {
		render_preview(frm);
		render_confidence(frm);
		add_review_buttons(frm);
	},
});

function render_preview(frm) {
	const field = frm.get_field("document_preview");
	if (!field || !field.$wrapper) return;
	const url = frm.doc.source_file;
	if (!url) {
		field.$wrapper.empty();
		return;
	}
	const safe = frappe.utils.escape_html(url);
	const lower = url.toLowerCase().split("?")[0];
	let inner;
	if (lower.endsWith(".pdf")) {
		inner = `<iframe src="${safe}" style="width:100%;height:600px;border:1px solid var(--border-color);border-radius:var(--border-radius-md);"></iframe>`;
	} else if (/\.(png|jpe?g|gif|webp|tiff?|bmp)$/.test(lower)) {
		inner = `<img src="${safe}" alt="document" style="max-width:100%;border:1px solid var(--border-color);border-radius:var(--border-radius-md);"/>`;
	} else {
		inner = `<a href="${safe}" target="_blank" rel="noopener">${__("Open document")}</a>`;
	}
	field.$wrapper.html(`<div class="mt-2 mb-2">${inner}</div>`);
}

function render_confidence(frm) {
	if (frm.doc.extraction_confidence === undefined || frm.doc.extraction_confidence === null) return;
	const pct = Math.round(frm.doc.extraction_confidence);
	const color = pct >= 80 ? "green" : pct >= 50 ? "orange" : "red";
	frm.dashboard.add_indicator(__("Extraction confidence: {0}%", [pct]), color);
}

function add_review_buttons(frm) {
	if (frm.is_new()) return;
	const status = frm.doc.status;
	const roles = frappe.user_roles || [];
	const can_review_items = roles.includes("Stock Manager") || roles.includes("System Manager");
	const can_approve = roles.includes("Accounts Manager") || roles.includes("System Manager");

	if (status === "Needs Item Review" && can_review_items) {
		frm.add_custom_button(__("Create Approved Items"), () => {
			call(frm, "approve_items", {}, (r) => {
				frappe.show_alert(
					{ message: __("{0} item(s) created · {1} pending", [r.created, r.pending]), indicator: "green" },
					6,
				);
			});
		}).addClass("btn-primary");
	}

	if (status === "Needs Review" && can_approve) {
		frm.add_custom_button(__("Approve"), () => {
			call(frm, "approve_document", {}, () => {
				frappe.show_alert({ message: __("Approved"), indicator: "green" }, 5);
			});
		}).addClass("btn-primary");
		frm.add_custom_button(__("Reject"), () => {
			frappe.prompt(
				[{ fieldtype: "Small Text", label: __("Reason"), fieldname: "reason" }],
				(v) => call(frm, "reject_document", { reason: v.reason }),
				__("Reject Document"),
				__("Reject"),
			);
		});
	}

	if (["Failed", "Needs Review", "Needs Item Review"].includes(status)) {
		frm.add_custom_button(__("Re-extract"), () => {
			call(frm, "reprocess", {}, () => {
				frappe.show_alert({ message: __("Re-extraction queued"), indicator: "blue" }, 5);
			});
		});
	}
}

function call(frm, method, args, on_success) {
	frappe.call({
		method: `erpnext_enhancements.accounting_intake.review.${method}`,
		args: Object.assign({ docname: frm.doc.name }, args || {}),
		freeze: true,
		callback: (r) => {
			if (r.exc) return;
			if (on_success) on_success(r.message || {});
			frm.reload_doc();
		},
	});
}
