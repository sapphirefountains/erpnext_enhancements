// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// The Water Feature Design form is thin since v1.768.0: the design is edited in the Fountain
// Designer page (/desk/fountain-designer?design=<name>), and this form shows what the last save
// derived. The rollups and the BOM grid are fields; this script adds the verdict headline, the
// readiness chips in Design Health, and the buttons (Open in Designer, Send design, the submittal packet).
// The old templates, live preview and loss dialogs went with the child tables they edited.

const WFD_STATUS_COLOR = { ok: "green", warn: "orange", blocked: "red", missing: "gray", n_a: "gray" };

frappe.ui.form.on("Water Feature Design", {
	refresh(frm) {
		frm.add_custom_button(__("Open in Designer"), () => wfd_open_designer(frm));
		if (!frm.is_new()) {
			if (frappe.model.can_email(frm.doctype, frm)) frm.add_custom_button(__("Send design"), () => wfd_send_design(frm));
			frm.add_custom_button(__("Preview Packet"), () => wfd_preview_packet(frm), __("Submittal"));
			frm.add_custom_button(__("Generate Packet PDF"), () => wfd_generate_packet(frm), __("Submittal"));
		}
		wfd_headline(frm);
		wfd_render_health(frm);
	},
});

function wfd_open_designer(frm) {
	// The page reads its place from route_options (router.place_from), so a new design opens the list.
	frappe.route_options = frm.is_new() ? {} : { design: frm.doc.name };
	frappe.set_route("fountain-designer");
}

function wfd_json(text, fallback) {
	if (!text) return fallback;
	if (typeof text === "object") return text;
	try {
		return JSON.parse(text);
	} catch (e) {
		return fallback;
	}
}

function wfd_headline(frm) {
	frm.dashboard.clear_headline();
	if (frm.is_new()) return;
	const results = wfd_json(frm.doc.results_json, {});
	const verdict = results.verdict || {};
	const blockers = cint(frm.doc.blocker_count);
	if (blockers > 0) {
		frm.dashboard.set_headline_alert(
			__("{0} blocking issue(s): this design cannot be Reviewed or issued. Fix them in the Designer.", [blockers]),
			"red"
		);
	} else if (verdict.headline) {
		frm.dashboard.set_headline_alert(frappe.utils.escape_html(verdict.headline), verdict.level === "good" ? "green" : "orange");
	}
}

function wfd_render_health(frm) {
	const field = frm.get_field("dashboard");
	if (!field || !field.$wrapper) return;
	if (frm.is_new()) {
		field.$wrapper.html(`<p class="text-muted">${__("Save, or open the design in the Designer, to size it.")}</p>`);
		return;
	}
	const esc = frappe.utils.escape_html;
	const readiness = wfd_json(frm.doc.readiness_json, {});
	const issues = wfd_json(frm.doc.design_issues_json, []);
	const chips = (readiness.sections || [])
		.map((s) => {
			const title = (s.missing || []).join("; ");
			return `<span class="indicator-pill ${WFD_STATUS_COLOR[s.status] || "gray"}" title="${esc(title)}">${esc(__(s.label || s.key))}</span>`;
		})
		.join(" ");
	const top = issues
		.filter((i) => i.severity !== "info")
		.slice(0, 8)
		.map((i) => {
			const signed = i.acknowledged ? ` <span class="text-muted">(${__("signed off")})</span>` : "";
			const color = i.severity === "blocker" ? "red" : "orange";
			return `<li><span class="indicator ${color}">${esc(i.title || i.key)}</span>${signed}${
				i.fix_hint ? `<div class="text-muted small">${esc(i.fix_hint)}</div>` : ""
			}</li>`;
		})
		.join("");
	const more = issues.filter((i) => i.severity === "info").length;
	field.$wrapper.html(`
		<div class="wfd-health">
			<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">${chips}</div>
			${top ? `<ul class="list-unstyled" style="margin:0">${top}</ul>` : `<p class="text-muted">${__("No blockers or warnings.")}</p>`}
			${more ? `<p class="text-muted small">${__("{0} advisory notes: open the Designer to read them.", [more])}</p>` : ""}
		</div>`);
}

// Send design (phase G): pick a paper, then frappe's own email composer on the record, with that print
// attached as a PDF (the composer's Attach Document Print, the path the Quotation's email takes), the
// customer's address, and a body in the email design system's chrome from api/designer.email_draft.
// The person sends it; the composer links the email to this design's timeline.
const WFD_API = "erpnext_enhancements.water_engineering.api.designer.";

function wfd_send_design(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the design first: the email attaches the saved design."));
		return;
	}
	const send = (print_format) =>
		frappe.call({
			method: WFD_API + "email_draft",
			args: { name: frm.doc.name, print_format },
			freeze: true,
			callback: (r) => {
				const m = r && r.message;
				if (!m) return;
				new frappe.views.CommunicationComposer({
					doc: frm.doc,
					frm,
					subject: m.subject,
					recipients: m.recipients || "",
					message: m.message,
					attach_document_print: true,
					print_format: m.print_format,
				});
			},
		});
	frappe.call({
		method: WFD_API + "email_draft",
		args: { name: frm.doc.name },
		callback: (r) => {
			const formats = ((r && r.message && r.message.formats) || []).map((f) => ({ value: f.print_format, label: f.label }));
			if (!formats.length) return;
			frappe.prompt(
				[{ fieldname: "print_format", fieldtype: "Select", label: __("Paper to send"), options: formats, default: formats[0].value, reqd: 1 }],
				(v) => send(v.print_format),
				__("Send design"),
				__("Write the email")
			);
		},
	});
}

function wfd_preview_packet(frm) {
	frappe.call({
		method: "erpnext_enhancements.water_engineering.packet.assemble_packet",
		args: { design: frm.doc.name },
		freeze: true,
		freeze_message: __("Assembling submittal packet…"),
		callback: (r) => {
			if (!r || !r.message) return;
			const w = window.open("", "_blank");
			if (w) {
				w.document.write(r.message);
				w.document.close();
			} else {
				frappe.msgprint(__("Allow pop-ups to preview the packet."));
			}
		},
	});
}

function wfd_generate_packet(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the design first: the packet is generated from the saved record."));
		return;
	}
	frappe.call({
		method: "erpnext_enhancements.water_engineering.packet.generate_packet",
		args: { design: frm.doc.name },
		freeze: true,
		freeze_message: __("Generating packet PDF…"),
		callback: (r) => {
			const m = r && r.message;
			if (m && m.packet_document) {
				frappe.show_alert({ message: __("Submittal packet generated."), indicator: "green" });
				window.open(m.packet_document, "_blank");
				frm.reload_doc();
			} else {
				frappe.msgprint(
					__("The packet HTML was stored, but the PDF could not be generated (see Error Log). Use Preview Packet for the browser-printable version.")
				);
			}
		},
	});
}
