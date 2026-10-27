/**
 * Opportunity form script (CRM enhancements).
 *
 * Targets: the Opportunity DocType form.
 * Loaded via: hooks.py `doctype_js["Opportunity"]`.
 *
 * Responsibilities:
 *  - Mirror the `custom_value_stream` child-table rows into the document's
 *    `_user_tags` so each value stream (Build/Design/Events/Service) shows up as a
 *    tag; for already-saved docs missing those tags it persists them directly via
 *    the `sync_opportunity_tags_for_existing` API instead of waiting for a save.
 *  - Reopen-on-load: an Opportunity already "Closed Won" with no project yet gets
 *    the next-step prompt once — "Schedule the hand-off meeting now?" while the
 *    hand-off is unbooked, "Create project now?" after (here "No" just
 *    dismisses). Fresh transitions into Closed Won are handled by the global
 *    realtime prompt in create_project_prompt.js. There is no manual "Create
 *    Project" button on the toolbar; project creation runs from that prompt or
 *    from the Hand-Off Process tab (opportunity_handoff.js).
 *  - Listen on the `project_creation_status` realtime channel to report
 *    background project-creation success/failure (incl. Google Drive folder
 *    provisioning results). Registered once, and quiet while the progress
 *    dialog in create_project_prompt.js is watching that creation (v1.747.0).
 */
function sync_tags_from_child_table(frm) {
	let value_streams = (frm.doc.custom_value_stream || [])
		.map((row) => row.value_stream)
		.filter(Boolean);
	let current_tags_str = frm.doc._user_tags || "";
	let current_tags = current_tags_str
		.split(",")
		.map((t) => t.trim())
		.filter(Boolean);

	let core_tags = ["Build", "Design", "Events", "Service", "Delivery", "Products"];
	let other_tags = current_tags.filter((t) => !core_tags.includes(t));

	let desired_tags = [...new Set([...other_tags, ...value_streams])];
	let new_tags_str = desired_tags.length ? "," + desired_tags.sort().join(",") + "," : null;

	if (frm.doc._user_tags !== new_tags_str) {
		let has_no_core_tags = !current_tags.some((t) => core_tags.includes(t));

		// If it's an existing document with missing tags, save it directly to the database via API.
		// Skipped while live collab applies a remote change: the originating
		// client performs this write; receivers only mirror tags locally.
		let applying_remote = frm._live_sync && frm._live_sync.applying_remote;
		if (
			!frm.is_new() &&
			!frm.is_dirty() &&
			has_no_core_tags &&
			value_streams.length > 0 &&
			!applying_remote
		) {
			frappe.call({
				method: "erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags_for_existing",
				args: { opportunity_name: frm.doc.name },
				callback: function (r) {
					if (r.message !== undefined) {
						frm.doc._user_tags = r.message;
						if (frm.tags && frm.tags.refresh) frm.tags.refresh(frm.doc._user_tags);
					}
				},
			});
		} else {
			// Update locally for real-time preview (will be saved when the form is saved)
			frm.doc._user_tags = new_tags_str;
			if (frm.tags && frm.tags.refresh) {
				frm.tags.refresh(frm.doc._user_tags);
			}
		}
	}
}

frappe.ui.form.on("Opportunity", {
	setup: function (frm) {
		// Dynamically bind to the child table's doctype for real-time updates when a row changes
		let child_df = frappe.meta.get_docfield("Opportunity", "custom_value_stream");
		if (child_df && child_df.options) {
			frappe.ui.form.on(child_df.options, {
				value_stream: function (child_frm, cdt, cdn) {
					sync_tags_from_child_table(child_frm);
				},
			});
		}
	},
	onload: function (frm) {
		// Capture the status the form loaded with, so the reopen-on-load prompt
		// only fires for opportunities that were ALREADY Closed Won when opened —
		// a fresh transition (handled by the realtime prompt) loads as non-won.
		frm._ee_loaded_status = frm.doc.status;
	},
	custom_value_stream_add: function (frm) {
		sync_tags_from_child_table(frm);
	},
	custom_value_stream_remove: function (frm) {
		sync_tags_from_child_table(frm);
	},
	refresh: function (frm) {
		// Sync tags on load
		sync_tags_from_child_table(frm);

		// Project creation is triggered by the next-step prompt
		// (create_project_prompt.js) or the Hand-Off Process tab, never a toolbar
		// button. Defensively drop any standard "Create > Project" entry if a
		// future ERPNext / site adds one.
		frm.remove_custom_button("Project", "Create");

		// Form kit P4-B: open this record in the guided step-by-step page. Only for a saved
		// record (step 1 of a new one is the page's own "new" screen) and only when this person
		// may open the page at all (frappe.boot.page_info lists the Pages their roles reach).
		if (!frm.is_new() && frappe.boot.page_info && frappe.boot.page_info["guided-entry"]) {
			frm.add_custom_button(__("Guided"), () => {
				// This record opens on the guided page again (guided_default.js remembered the form),
				// stepping back rather than pushing when the form was opened from the guided page.
				if (window.sf_guided && window.sf_guided.to_guided) {
					window.sf_guided.to_guided(frm.doctype, frm.doc.name);
					return;
				}
				frappe.set_route("guided-entry", frm.doctype, frm.doc.name);
			});
		}

		// Reopen-on-load: an opportunity already Closed Won with no project yet
		// gets the same prompt once ("No" just dismisses — it was won intentionally
		// earlier). Guarded to fire at most once per load and never for a status
		// change made during this session (the realtime prompt covers transitions).
		// Never for a historical deal (pre-ERPNext, untouched for 90 days —
		// crm_enhancements/historical.py, v1.629.0): it is excluded from every
		// won-without-project warning, and opening one to read it is not a hand-off.
		if (
			!frm._ee_reopen_checked &&
			!frm.is_new() &&
			frm._ee_loaded_status === "Closed Won" &&
			frm.doc.status === "Closed Won" &&
			!frm.doc.custom_created_project &&
			!frm.doc.custom_is_historical &&
			erpnext_enhancements.crm &&
			erpnext_enhancements.crm.confirm_won_next_step
		) {
			frm._ee_reopen_checked = true;
			erpnext_enhancements.crm.confirm_won_next_step(frm.doc.name, {
				mode: "reopen",
				frm: frm,
			});
		}
	},
});

// The real-time listener for completion, registered once (v1.747.0). It was registered in
// `refresh`, which runs on every load and reload, so a form opened a few times answered one
// creation with that many alerts and reloads — the reload after a creation itself added one.
//
// While the progress dialog (create_project_prompt.js) watches this creation it says how it
// went and reloads the record; this stands aside. Otherwise — the dialog closed early, or the
// event is for someone in the notify list — this reports it with the dialog's own words, and
// reloads the form unless it holds unsaved edits.
if (!frappe._ee_opportunity_creation_listener) {
	frappe._ee_opportunity_creation_listener = true;
	frappe.realtime.on("project_creation_status", function (data) {
		const crm = window.erpnext_enhancements && erpnext_enhancements.crm;
		if (!data || !crm || !crm.report_project_creation_status) return;
		if (crm.is_tracking_project_creation && crm.is_tracking_project_creation(data.opportunity_name)) return;
		const view = frappe.views && frappe.views.formview && frappe.views.formview.Opportunity;
		const frm = view && view.frm;
		if (!frm || !frm.doc || frm.doc.name !== data.opportunity_name) return;
		// The hand-off gate's refusal (PRO-0204) comes through as its own status, so the user is
		// told what to do instead of being handed "creation failed" for a process step they can
		// complete in two clicks on this very form.
		crm.report_project_creation_status(data, frm);
	});
}
