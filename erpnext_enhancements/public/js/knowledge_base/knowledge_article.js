// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Knowledge Article form: Start Revision, Confirm Still Accurate, Retire (WI-080 PR 3, ADR 0017).
//
// Every staff member reads this form, and most see no button at all. Which buttons appear comes
// from frm.doc.__onload.kb.actions, which the controller's onload (knowledge_base/
// publish.article_onload) fills with the actions the server's own rules allow this person now:
//
// * Start Revision (KB Author, KB Approver): a new Draft copied from the live version, or the one
//   already open (one open version per article); either way the form routes to it.
// * Confirm Still Accurate (the process owner, or a KB Approver, from a browser): the next review
//   falls due one review interval from today.
// * Retire (a KB Approver, from a browser, with no revision open): readers see it marked Retired,
//   with the reason. Nothing is deleted.
//
// Every endpoint checks again. Routes go through frappe.set_route, so Back returns here.
//
// 2026-09-30: the top of the form is the article as a document, laid out as its kind's company
// register template (POL-0002 Policy, POL-0003 Process, POL-0004 SOP): the same page the printer and
// the PDF give. The server draws it (publish.article_onload -> knowledge_base/printing.article_html,
// every value escaped there, the body the stored text the Version controller cleaned) and this only
// puts it in the document_view field. The fields under it are collapsed; if the drawing failed
// (__onload.kb.document is null) the Article Text section opens instead, so nothing is ever hidden.
// Print / PDF opens the print view (frm.print_doc, a route, so Back returns here), whose default
// format is "Article Document".

const KB_METHOD = "erpnext_enhancements.api.knowledge_base.";

frappe.ui.form.on("Knowledge Article", {
	refresh(frm) {
		if (frm.is_new()) return;
		const kb = (frm.doc.__onload && frm.doc.__onload.kb) || {};
		const can = new Set(kb.actions || []);
		kb_article_intro(frm, kb);
		kb_draw_document(frm, kb);
		if (frappe.model.can_print(frm.doctype, frm)) {
			frm.add_custom_button(__("Print / PDF"), () => frm.print_doc());
		}

		if (can.has("start_revision")) {
			const open = kb.open_version;
			const button = frm.add_custom_button(
				open ? __("Open Revision {0}", [open]) : __("Start Revision"),
				() => kb_start_revision(frm)
			);
			if (!open) button.addClass("btn-primary");
		}
		if (can.has("confirm_still_accurate")) {
			frm.add_custom_button(__("Confirm Still Accurate"), () => kb_confirm(frm));
		}
		if (can.has("retire")) {
			frm.add_custom_button(__("Retire"), () => kb_retire(frm), __("Actions"));
		}
	},
});

function kb_start_revision(frm) {
	kb_call("start_revision", { article: frm.doc.name }, __("Opening a revision...")).then((r) => {
		const out = r.message || {};
		if (!out.created) {
			frappe.show_alert(
				{
					message: __("{0} is already open ({1}); one revision at a time.", [
						kb_escape(out.version),
						kb_escape(__(out.review_state || "")),
					]),
					indicator: "blue",
				},
				6
			);
		}
		frappe.set_route("Form", "Knowledge Article Version", out.version);
	});
}

function kb_confirm(frm) {
	const months = frm.doc.review_every_months || 6;
	frappe.confirm(
		__(
			"Confirm {0} is still accurate? You are saying you have read it and it still describes how Sapphire works. Its next review falls due {1} months from today.",
			[kb_escape(frm.doc.name), kb_escape(String(months))]
		),
		() =>
			kb_call("confirm_still_accurate", { article: frm.doc.name }, __("Recording the review...")).then(() => {
				frappe.show_alert({ message: __("Review recorded."), indicator: "green" }, 5);
				frm.reload_doc();
			})
	);
}

function kb_retire(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Retire {0}", [kb_escape(frm.doc.name)]),
		fields: [
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Why is it being retired?"),
				reqd: 1,
				// 2026-09-29: an article's number carries its kind and department, and never changes.
				// Reclassifying or moving one is a new article, published first, and this one retired
				// naming it, so a citation of the old number still leads somewhere.
				description: __(
					"Every reader sees this reason on the article. It keeps its number and its history, and nothing is deleted. If a new article replaces this one (to change its kind or department, for instance), publish the new one first and name it here."
				),
			},
		],
		primary_action_label: __("Retire"),
		primary_action(values) {
			kb_call("retire", { article: frm.doc.name, reason: values.reason }, __("Retiring...")).then(() => {
				dialog.hide();
				frappe.show_alert({ message: __("Retired."), indicator: "gray" }, 5);
				frm.reload_doc();
			});
		},
	});
	dialog.show();
}

function kb_article_intro(frm, kb) {
	if (frm.doc.status === "Retired") {
		frm.set_intro(
			__("Retired on {0} by {1}: {2}", [
				kb_escape(frappe.datetime.str_to_user(frm.doc.retired_on) || ""),
				kb_escape(frappe.user.full_name(frm.doc.retired_by) || frm.doc.retired_by || ""),
				kb_escape(frm.doc.retired_reason || ""),
			]),
			"red"
		);
	} else if (frm.doc.review_by && frm.doc.review_by < frappe.datetime.get_today()) {
		frm.set_intro(
			__("Its review was due on {0}.", [kb_escape(frappe.datetime.str_to_user(frm.doc.review_by))]),
			"orange"
		);
	} else if (kb.open_version) {
		frm.set_intro(__("A revision is open: {0} ({1}).", [kb_escape(kb.open_version), kb_escape(__(kb.open_state || ""))]), "blue");
	} else {
		frm.set_intro();
	}
}

function kb_draw_document(frm, kb) {
	const field = frm.get_field("document_view");
	if (!field || !field.$wrapper) return;
	const section = frm.fields_dict.section_body;
	if (kb.document) {
		field.$wrapper.html(kb.document);
		// The form is reused for every article in the session: an Article Text section this opened
		// for an article whose document failed goes back to collapsed, unless the person opened it.
		if (section && section.__kb_opened) {
			section.__kb_opened = false;
			section.expanded_by_user = false;
			section.collapse(true);
		}
		return;
	}
	field.$wrapper.html(
		`<p class="text-muted">${kb_escape(
			__("The document view could not be drawn. The article's text is under Article Text below.")
		)}</p>`
	);
	// expanded_by_user keeps it open: v16's layout re-collapses every collapsible section on each
	// refresh unless the person opened it (layout.js refresh_section_collapse).
	if (section && section.collapse) {
		section.__kb_opened = section.__kb_opened || !section.expanded_by_user;
		section.expanded_by_user = true;
		section.collapse(false);
	}
}

function kb_call(method, args, freeze_message) {
	return frappe.call({
		method: KB_METHOD + method,
		type: "POST",
		args,
		freeze: true,
		freeze_message,
	});
}

function kb_escape(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}
