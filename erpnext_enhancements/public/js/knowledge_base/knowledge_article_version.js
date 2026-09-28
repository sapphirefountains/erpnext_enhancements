// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Knowledge Article Version form: the review actions (WI-080 PR 3, ADR 0017).
//
// Every button calls erpnext_enhancements.api.knowledge_base, which checks everything again: a
// whitelisted method can be called directly, whatever buttons a form draws. Which buttons appear
// is NOT decided here. The controller's onload (knowledge_base/publish.version_onload) puts in
// frm.doc.__onload.kb.actions exactly the actions the same server rules (workflow.*_problems)
// allow this person now, so a button is never offered for a move the server would refuse, and no
// rule is written twice.
//
// Approve sends frm.doc.modified, the version as this page loaded it. If anyone changed it since,
// the server refuses ("it changed after you opened it") and the approver reloads and reads again.
//
// The comment box is hidden: a Comment on a draft is refused (knowledge_base/references.py),
// because every System Manager, and every AI tool acting for one, can read Comments. Request
// Changes carries the reviewer's note instead, and it stays on the version.
//
// Frappe's own Discard is taken off the menu and refused here too (v1.556.1). v16 puts it on the
// menu of every submittable draft (form/toolbar.js:385-397), next to the KB's Actions > Discard, and
// it would set docstatus 2 behind the state machine's back, leaving a version that reads as open
// and can never be saved again. The server refuses it whatever the form does (the controller's
// before_discard / on_discard); this only spares the person the click.
//
// No screen of its own: every action is a dialog over the form or a route to another form
// (frappe.set_route, which gives it a history entry), so Back and Forward work as they do
// everywhere else in the Desk.

const KB_METHOD = "erpnext_enhancements.api.knowledge_base.";

frappe.ui.form.on("Knowledge Article Version", {
	refresh(frm) {
		kb_hide_comment_box(frm);
		kb_hide_native_discard(frm);
		if (frm.is_new()) return;

		const kb = (frm.doc.__onload && frm.doc.__onload.kb) || {};
		const can = new Set(kb.actions || []);
		const state = frm.doc.review_state || "Draft";

		// Content changes only in Draft; the server refuses anything else, so the form says so
		// rather than letting someone type into a version that cannot be saved. (A submitted
		// version is read-only already.)
		if (frm.doc.docstatus === 0 && state !== "Draft") {
			frm.disable_save();
			frm.set_read_only();
		}
		kb_version_intro(frm, kb, state);

		if (can.has("submit_for_review")) {
			frm.add_custom_button(__("Submit for Review"), () => kb_submit(frm)).addClass("btn-primary");
		}
		if (can.has("approve_and_publish")) {
			frm.add_custom_button(__("Approve and Publish"), () => kb_approve(frm)).addClass("btn-primary");
		}
		if (can.has("request_changes")) {
			frm.add_custom_button(__("Request Changes"), () => kb_request_changes(frm));
		}
		if (can.has("review_diff")) {
			frm.add_custom_button(__("View Changes"), () => kb_show_diff(frm));
		}
		if (can.has("withdraw")) {
			frm.add_custom_button(__("Withdraw"), () => kb_withdraw(frm), __("Actions"));
		}
		if (can.has("discard")) {
			frm.add_custom_button(__("Discard"), () => kb_discard(frm), __("Actions"));
		}
		if (frm.doc.article) {
			frm.add_custom_button(__("Open Article {0}", [frm.doc.article]), () =>
				frappe.set_route("Form", "Knowledge Article", frm.doc.article)
			);
		}
	},

	// Frappe's own Discard, should it be reached some other way than the menu item removed above
	// (form.js _discard runs this and stops when frappe.validated is false). Never the KB's own
	// Discard, which is a kb_call and runs no form event.
	before_discard() {
		frappe.validated = false;
		frappe.msgprint({
			title: __("Discard through the knowledge base"),
			indicator: "orange",
			message: __(
				"Use Actions > Discard on a draft, or Withdraw to take it out of review first. This menu item would leave the version stuck."
			),
		});
	},
});

// ------------------------------------------------------------------ the actions

function kb_submit(frm) {
	const go = () =>
		kb_call("submit_for_review", { version: frm.doc.name }, __("Sending for review...")).then((r) => {
			const names = ((r.message && r.message.notified) || []).map((p) => p.full_name || p.user);
			if (names.length) {
				frappe.show_alert(
					{
						message: __("Sent for review. Asked: {0}.", [kb_escape(names.join(", "))]),
						indicator: "green",
					},
					7
				);
			} else {
				frappe.msgprint({
					title: __("Sent for review"),
					indicator: "orange",
					message: __(
						"It is in review, but no KB Approver is free to approve it: every one of them created, submitted or changed it. Ask Nik to grant KB Approver to someone who has not worked on it."
					),
				});
			}
			frm.reload_doc();
		});
	// Unsaved edits first: what is reviewed is what is stored. v16's frm.save() resolves even when
	// the server refuses the save (form.js:850-851 resolve with no on_error, whatever r.exc says), so
	// the promise alone would send the older stored copy for review and the reload after it would
	// throw the edits away. Only a save the server accepted syncs the stored doc back and clears
	// __unsaved (model/sync.js:240), so a form still dirty afterwards means it was refused: stop,
	// and leave the edits on screen with the refusal.
	if (frm.is_dirty()) {
		frm.save().then(() => {
			if (!frm.is_dirty()) go();
		});
	} else {
		go();
	}
}

function kb_approve(frm) {
	const into = frm.doc.article
		? __("the new version of {0}", [kb_escape(frm.doc.article)])
		: __("a new article in {0}", [kb_escape(frm.doc.department_block || "")]);
	frappe.confirm(
		__(
			"Publish {0} as {1}? Every staff member and every AI tool Sapphire uses will read it. If you have not read what changed, press No and use View Changes first.",
			[kb_escape(frm.doc.title || frm.doc.name), into]
		),
		() =>
			kb_call(
				"approve_and_publish",
				// The version as this page loaded it: a copy that changed since is refused.
				{ version: frm.doc.name, modified: frm.doc.modified },
				__("Publishing...")
			).then((r) => {
				const out = r.message || {};
				frappe.show_alert(
					{
						message: __("Published {0}, version {1}.", [
							kb_escape(out.article),
							kb_escape(String(out.version_number)),
						]),
						indicator: "green",
					},
					7
				);
				frappe.set_route("Form", "Knowledge Article", out.article);
			})
	);
}

function kb_request_changes(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Request Changes"),
		fields: [
			{
				fieldname: "note",
				fieldtype: "Small Text",
				label: __("What needs to change"),
				reqd: 1,
				description: __(
					"The author sees this on the draft. It stays with the version, where only KB Authors and KB Approvers can read it, and is never copied into a to-do or an email."
				),
			},
		],
		primary_action_label: __("Send Back"),
		primary_action(values) {
			kb_call(
				"request_changes",
				{ version: frm.doc.name, note: values.note },
				__("Sending back...")
			).then(() => {
				dialog.hide();
				frappe.show_alert({ message: __("Sent back to the author."), indicator: "green" }, 5);
				frm.reload_doc();
			});
		},
	});
	dialog.show();
}

function kb_withdraw(frm) {
	frappe.confirm(
		__("Take {0} back out of review? It returns to Draft, and its review to-dos close.", [
			kb_escape(frm.doc.name),
		]),
		() =>
			kb_call("withdraw", { version: frm.doc.name }, __("Withdrawing...")).then(() => {
				frappe.show_alert({ message: __("Back to Draft."), indicator: "blue" }, 5);
				frm.reload_doc();
			})
	);
}

function kb_discard(frm) {
	frappe.confirm(
		__(
			"Discard {0}? It is kept as history and can never be edited again. To change the article later, start a new revision from it.",
			[kb_escape(frm.doc.name)]
		),
		() =>
			kb_call("discard", { version: frm.doc.name }, __("Discarding...")).then(() => {
				frappe.show_alert({ message: __("Discarded."), indicator: "gray" }, 5);
				frm.reload_doc();
			})
	);
}

function kb_show_diff(frm) {
	kb_call("review_diff", { version: frm.doc.name }, __("Comparing..."), "GET").then((r) => {
		const out = r.message || {};
		const dialog = new frappe.ui.Dialog({
			title: out.article
				? __("{0} against the published {1}", [kb_escape(out.version), kb_escape(out.article)])
				: __("{0}: a new article", [kb_escape(out.version)]),
			size: "extra-large",
			fields: [{ fieldtype: "HTML", fieldname: "diff" }],
		});
		dialog.fields_dict.diff.$wrapper.html(kb_diff_html(out));
		dialog.show();
	});
}

// ------------------------------------------------------------------ what the form says

function kb_version_intro(frm, kb, state) {
	const who = (user) => kb_escape(frappe.user.full_name(user) || user || "");
	const blockers = kb.approve_blockers || [];
	if (state === "Draft") {
		const lines = [];
		if (frm.doc.review_note) {
			// The latest note stays on the version (it is kept, not cleared), so it is labelled as
			// the last one rather than as a request still open.
			lines.push(
				__("Last review note, from {0}: {1}", [who(frm.doc.reviewer), kb_escape(frm.doc.review_note)])
			);
		}
		// WI-080 PR 5: why Submit for Review is not offered (publish.version_onload sends the same
		// workflow.submit_problems the button is judged by). Every draft open when the article kind
		// arrived has none, and without this the button would just be missing.
		const submit_blockers = kb.submit_blockers || [];
		if (submit_blockers.length) {
			lines.push(
				__("Before it can be submitted for review: {0}.", [submit_blockers.map(kb_escape).join("; ")])
			);
		}
		if (lines.length) {
			frm.set_intro(lines.join("<br>"), "orange");
		} else {
			frm.set_intro();
		}
	} else if (state === "In Review") {
		let text = __("In review, submitted by {0}. A KB Approver who did not write it approves it.", [
			who(frm.doc.submitted_by),
		]);
		if (blockers.length) {
			text +=
				"<br>" +
				__("You cannot approve it: {0}.", [blockers.map(kb_escape).join("; ")]);
		}
		frm.set_intro(text, blockers.length ? "orange" : "blue");
	} else if (state === "Published") {
		frm.set_intro(
			__("Published as {0}, version {1}, approved by {2}.", [
				kb_escape(frm.doc.article || ""),
				kb_escape(String(frm.doc.version_number || "")),
				who(frm.doc.approved_by),
			]),
			"green"
		);
	} else if (state === "Superseded") {
		frm.set_intro(__("Superseded: a newer version of {0} is live. Kept as history.", [kb_escape(frm.doc.article || "")]), "gray");
	} else if (state === "Discarded") {
		frm.set_intro(__("Discarded. Kept as history; it cannot be edited again."), "gray");
	} else {
		frm.set_intro();
	}
}

function kb_diff_html(out) {
	const parts = [];
	if (!out.article) {
		parts.push(`<p class="text-muted">${kb_escape(__("This is a first version, so all of it is new."))}</p>`);
	}
	const fields = out.fields || [];
	if (fields.length) {
		parts.push(
			`<table class="table table-bordered"><thead><tr><th>${kb_escape(__("Field"))}</th><th>${kb_escape(
				__("Published")
			)}</th><th>${kb_escape(__("This version"))}</th></tr></thead><tbody>`
		);
		for (const f of fields) {
			parts.push(
				`<tr><td>${kb_escape(__(f.label))}</td><td>${kb_escape(f.before)}</td><td>${kb_escape(f.after)}</td></tr>`
			);
		}
		parts.push("</tbody></table>");
	}
	if (out.change_note) {
		parts.push(`<p><b>${kb_escape(__("Change note"))}:</b> ${kb_escape(out.change_note)}</p>`);
	}
	const lines = out.body || [];
	if (!lines.length) {
		parts.push(`<p class="text-muted">${kb_escape(__("The text is unchanged."))}</p>`);
	} else {
		const rows = lines.map((line) => {
			let style = "";
			if (line.startsWith("+")) style = "background: var(--bg-green, #e6f4ea);";
			else if (line.startsWith("-")) style = "background: var(--bg-red, #fdecea);";
			else if (line.startsWith("@@")) style = "color: var(--text-muted, #6c757d);";
			return `<div style="${style}">${kb_escape(line) || "&nbsp;"}</div>`;
		});
		parts.push(
			`<div style="white-space: pre-wrap; word-break: break-word; font-family: var(--font-family-monospace, monospace); font-size: 0.85em;">${rows.join(
				""
			)}</div>`
		);
	}
	return parts.join("");
}

// ------------------------------------------------------------------ helpers

function kb_call(method, args, freeze_message, type) {
	// Server refusals arrive as a frappe.throw: Frappe shows its message (one sentence naming
	// every broken rule) and the promise never resolves, so nothing after it runs.
	return frappe.call({
		method: KB_METHOD + method,
		type: type || "POST",
		args,
		freeze: true,
		freeze_message,
	});
}

function kb_escape(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}

function kb_hide_comment_box(frm) {
	const $box = frm.footer && frm.footer.wrapper && frm.footer.wrapper.find(".comment-box");
	if ($box && $box.length) $box.toggle(false);
}

function kb_hide_native_discard(frm) {
	// v16 rebuilds the menu before every "refresh" event (form.js:623-630: refresh_header, then the
	// trigger), so removing it here removes it every time. Exact label only, and never a
	// user-action row: add_custom_button copies Actions > Discard into the menu for small screens.
	const $menu = frm.page && frm.page.menu;
	if (!$menu || !$menu.find) return;
	const label = __("Discard");
	$menu.find(".menu-item-label").each(function () {
		if ($(this).text().trim() === label) $(this).closest("li").not(".user-action").remove();
	});
}
