/**
 * Project Folder Google Drive Settings — admin tools.
 *
 * Targets: this Single doctype's form.
 * Loaded via: standard doctype form script (same-folder .js).
 *
 * Adds buttons backed by crm_enhancements.drive_sync:
 *  - Test Connection: validates the service-account JSON, the Drive API, and
 *    access to each configured Drive/folder; shows per-check results with the
 *    service-account email to add as a Shared Drive member when missing.
 *  - Link Existing Folders: queues the blind by-name backfill that links
 *    pre-existing Customers/Projects to their Drive folders.
 *  - Drive Link Manager: opens the reviewed bulk-linking dashboard
 *    (/app/drive-link-manager) — fuzzy-matched, manual-review, fault-tolerant.
 */
frappe.ui.form.on("Project Folder Google Drive Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "erpnext_enhancements.crm_enhancements.drive_sync.test_connection",
				freeze: true,
				freeze_message: __("Checking Google Drive access..."),
			}).then((r) => {
				const res = r.message || {};
				const esc = frappe.utils.escape_html;
				const rows = (res.checks || [])
					.map((c) => {
						const icon = c.ok === true ? "✅" : c.ok === false ? "❌" : "➖";
						return `<tr><td>${icon}</td><td>${esc(c.check)}</td><td>${esc(c.detail || "")}</td></tr>`;
					})
					.join("");
				frappe.msgprint({
					title: __("Google Drive Connection"),
					message: `
						${res.service_account ? `<p><b>${__("Service account")}:</b> ${esc(res.service_account)}</p>` : ""}
						<table class="table table-bordered"><tbody>${rows}</tbody></table>`,
					wide: true,
				});
			});
		});

		frm.add_custom_button(__("Link Existing Folders"), () => {
			frappe.confirm(
				__("Scan Drive and link existing Customers and Projects to their folders by name? (Nothing is created or modified in Drive.)"),
				() => {
					frappe.call({
						method: "erpnext_enhancements.crm_enhancements.drive_sync.backfill_drive_links",
					}).then(() => {
						frappe.show_alert({
							message: __("Backfill queued — results land in the Drive Sync Log."),
							indicator: "green",
						});
					});
				}
			);
		});

		// The reviewed, fuzzy-matched alternative to the blind backfill above.
		frm.add_custom_button(__("Drive Link Manager"), () => {
			frappe.set_route("drive-link-manager");
		}).addClass("btn-primary");
	},
});
