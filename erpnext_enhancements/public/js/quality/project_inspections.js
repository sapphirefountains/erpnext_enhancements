/*
 * Project form: which inspection milestones have come round, and what is in the way.
 *
 * Reads api/quality_due.get_project_milestones, which recomputes from current state rather
 * than storing anything. Nothing here generates an inspection -- generating one stays a
 * deliberate act, the same way severity is never guessed. The button opens the milestone so a
 * person decides.
 *
 * It shows EVERY milestone, not only the due ones. A list of just what is due answers "what
 * should I do now" and silently loses the two questions worth more: why has that one not come
 * round yet, and why is that one not showing at all.
 *
 * Two states here are findings rather than statuses, and both are said plainly:
 *
 *   blocked  -- due, and nobody has ever written the checklist for it. Only the Build
 *               commissioning list exists today. Hiding these would turn a gap in what the
 *               company has written down into a gap nobody can see.
 *   unknown  -- the project's build status is blank or is not one of the known options, so
 *               whether the milestone has come round cannot be worked out. Reporting this as
 *               "not due" is what would make the whole sweep read clean forever.
 */

frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		load_inspection_milestones(frm);
	},
});

function load_inspection_milestones(frm) {
	frappe.call({
		method: "erpnext_enhancements.api.quality_due.get_project_milestones",
		args: { project: frm.doc.name },
		callback: function (r) {
			const data = r && r.message;
			if (!data || !data.enabled) return;
			if (!data.milestones || !data.milestones.length) return;
			render_milestone_panel(frm, data);
		},
	});
}

function render_milestone_panel(frm, data) {
	const counts = data.counts || {};
	if (counts.due) {
		const parts = [__("{0} inspection milestones are ready", [counts.due])];
		if (counts.blocked) {
			// Counted separately on purpose: "three are due" and "three are due and one has no
			// checklist" are different sentences, and only the second needs somebody to do
			// something other than inspect.
			parts.push(__("{0} with no checklist written yet", [counts.blocked]));
		}
		frm.dashboard.set_headline_alert(parts.join(" &middot; "), counts.blocked ? "orange" : "blue");
	} else if (counts.unknown) {
		frm.dashboard.set_headline_alert(
			__("Inspection milestones cannot be worked out: this project has no recognised build status."),
			"orange"
		);
	}

	frm.add_custom_button(
		__("Inspection milestones"),
		function () {
			show_milestone_dialog(frm, data);
		},
		__("Quality")
	);
}

const STATE_LABEL = {
	due: ["Ready", "blue"],
	done: ["Done", "green"],
	waiting: ["Not yet", "gray"],
	manual: ["When you decide", "gray"],
	skipped: ["Does not apply", "gray"],
	unknown: ["Cannot tell", "orange"],
};

function show_milestone_dialog(frm, data) {
	const rows = data.milestones
		.map(function (row) {
			const label = STATE_LABEL[row.state] || [row.state, "gray"];
			const chip = `<span class="indicator-pill ${label[1]}">${__(label[0])}</span>`;
			const blocked = row.blocked
				? `<div class="text-danger small">${__("No checklist has been written for this milestone yet.")}</div>`
				: "";
			return `<tr>
				<td style="padding:8px 10px;vertical-align:top;">
					<b>${frappe.utils.escape_html(row.title)}</b>
					<div class="text-muted small">${frappe.utils.escape_html(row.reason || "")}</div>
					${blocked}
				</td>
				<td style="padding:8px 10px;white-space:nowrap;vertical-align:top;">${chip}</td>
			</tr>`;
		})
		.join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Inspection milestones"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<div style="overflow-x:auto;">
					<table class="table table-borderless" style="margin:0;">${rows}</table>
				</div>
				<p class="text-muted small" style="margin-top:12px;">
					${__("Nothing is generated automatically. Open the Quality Control workspace to generate an inspection for a milestone that is ready.")}
				</p>`,
			},
		],
	});
	dialog.show();
}
