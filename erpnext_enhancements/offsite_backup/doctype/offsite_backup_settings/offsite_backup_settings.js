/**
 * Client script for the Offsite Backup Settings form.
 *
 * Two buttons and one table:
 *  - "Test Connection" reports the folder, whether it is on a Shared Drive, how
 *    many objects are already in it, and every problem that would otherwise only
 *    surface as a failed backup at 02:00.
 *  - "Run Backup Now" prompts for the tier and queues it on the long queue.
 *  - A "Recent Runs" dashboard section lists the last 5 log rows, colour-coded,
 *    so the page answers "is this actually working?" without navigating away.
 *    That question is the whole reason anyone opens this form.
 */
const SETTINGS_API =
	"erpnext_enhancements.offsite_backup.doctype.offsite_backup_settings.offsite_backup_settings";
const BACKUP_API = "erpnext_enhancements.offsite_backup.backup";

const STATUS_COLOURS = {
	Success: "green",
	Failed: "red",
	Running: "blue",
	Skipped: "orange",
};

// Not named `escape`: Frappe evaluates a doctype's sibling .js as a classic global
// <script>, so a top-level `function escape()` would permanently overwrite the
// window.escape builtin for the rest of the desk session.
function escapeHtml(value) {
	return frappe.utils.escape_html(String(value === undefined || value === null ? "" : value));
}

function formatDuration(seconds) {
	if (!seconds) {
		return "—";
	}
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const secs = Math.floor(seconds % 60);
	if (hours) {
		return `${hours}h ${minutes}m`;
	}
	if (minutes) {
		return `${minutes}m ${secs}s`;
	}
	return `${secs}s`;
}

function formatBytes(bytes) {
	if (!bytes) {
		return "—";
	}
	const gib = bytes / (1024 * 1024 * 1024);
	if (gib >= 1) {
		return `${gib.toFixed(2)} GB`;
	}
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function testConnection() {
	frappe.call({
		method: `${SETTINGS_API}.test_connection`,
		freeze: true,
		freeze_message: __("Checking the Drive folder…"),
		callback(r) {
			const m = r.message;
			if (!m) {
				return;
			}
			const lines = [
				`<b>${__("Folder")}:</b> ${escapeHtml(m.folder_name || "?")}`,
				`<b>${__("On a Shared Drive")}:</b> ${m.shared_drive ? __("yes") : __("no")}`,
				`<b>${__("Objects already in the folder")}:</b> ${
					m.object_count === null || m.object_count === undefined ? __("unknown") : m.object_count
				}`,
			];
			if (m.problems && m.problems.length) {
				lines.push(
					`<hr><b>${__("Problems")}:</b><ul>` +
						m.problems.map((p) => `<li>${escapeHtml(p)}</li>`).join("") +
						"</ul>",
				);
			} else {
				lines.push(`<hr>${__("No problems found. Uploads and pruning should both work.")}`);
			}
			frappe.msgprint({
				title: m.ok ? __("Connection OK") : __("Connection has problems"),
				indicator: m.ok ? "green" : "red",
				message: lines.join("<br>"),
			});
		},
	});
}

function runBackupNow(frm) {
	frappe.prompt(
		[
			{
				fieldname: "tier",
				fieldtype: "Select",
				label: __("What to back up"),
				options: ["Database only", "Full (database + files)"],
				default: "Full (database + files)",
				reqd: 1,
			},
		],
		(values) => {
			const backup_type = values.tier === "Database only" ? "Manual" : "Manual Full";
			frappe.call({
				method: `${BACKUP_API}.run_backup_now`,
				args: { backup_type },
				freeze: true,
				freeze_message: __("Queueing the backup…"),
				callback(r) {
					const m = r.message || {};
					frappe.msgprint({
						title: __("Backup queued"),
						indicator: "blue",
						message: m.message || __("Queued on the long queue."),
					});
					// The worker has to pick the job up before a row appears; give
					// it a moment or the table just redraws its previous state.
					setTimeout(() => renderRecentRuns(frm), 3000);
				},
			});
		},
		__("Run Backup Now"),
		__("Start Backup"),
	);
}

function renderRecentRuns(frm) {
	frappe.db
		.get_list("Offsite Backup Log", {
			fields: [
				"name",
				"backup_type",
				"status",
				"started_at",
				"duration_seconds",
				"files_uploaded",
				"bytes_uploaded",
				"pruned_count",
			],
			order_by: "creation desc",
			limit: 5,
		})
		.then((rows) => {
			const body = (rows || []).length
				? rows
						.map((row) => {
							const colour = STATUS_COLOURS[row.status] || "gray";
							const link = `/app/offsite-backup-log/${encodeURIComponent(row.name)}`;
							return `<tr>
								<td><a href="${link}">${escapeHtml(row.name)}</a></td>
								<td>${escapeHtml(row.backup_type)}</td>
								<td><span class="indicator-pill ${colour}">${escapeHtml(row.status)}</span></td>
								<td>${row.started_at ? escapeHtml(frappe.datetime.str_to_user(row.started_at)) : "—"}</td>
								<td>${formatDuration(row.duration_seconds)}</td>
								<td>${row.files_uploaded || 0}</td>
								<td>${formatBytes(row.bytes_uploaded)}</td>
								<td>${row.pruned_count || 0}</td>
							</tr>`;
						})
						.join("")
				: `<tr><td colspan="8" class="text-muted">${__("No runs yet.")}</td></tr>`;

			const html = `<div class="offsite-recent-runs table-responsive">
				<table class="table table-bordered small">
					<thead><tr>
						<th>${__("Run")}</th><th>${__("Type")}</th><th>${__("Status")}</th>
						<th>${__("Started")}</th><th>${__("Duration")}</th>
						<th>${__("Files")}</th><th>${__("Uploaded")}</th><th>${__("Pruned")}</th>
					</tr></thead>
					<tbody>${body}</tbody>
				</table>
			</div>`;

			// The form's refresh event can fire more than once per page; drop any
			// section we already rendered so the table never stacks up.
			// `parent`, not `wrapper` — FormDashboard only ever assigns `this.parent`
			// (frappe/public/js/frappe/form/dashboard.js), so `wrapper` is undefined
			// and dereferencing it throws inside this .then() before add_section runs,
			// leaving the table silently missing on every load.
			const existing = frm.dashboard.parent.find(".offsite-recent-runs");
			if (existing.length) {
				const section = existing.closest(".form-dashboard-section");
				(section.length ? section : existing).remove();
			}
			frm.dashboard.add_section(html, __("Recent Runs"));
		});
}

frappe.ui.form.on("Offsite Backup Settings", {
	refresh(frm) {
		renderRecentRuns(frm);

		frm.dashboard.clear_headline();
		if (!frm.doc.enabled) {
			frm.dashboard.set_headline(
				__(
					"Offsite backups are switched off — nothing is scheduled. Run Backup Now still works, so you can prove the setup before enabling it.",
				),
			);
		}

		frm.add_custom_button(__("Offsite Backup Log"), () => {
			frappe.set_route("List", "Offsite Backup Log");
		});
	},

	test_connection() {
		testConnection();
	},

	run_backup_now(frm) {
		runBackupNow(frm);
	},
});
