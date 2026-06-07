/* global frappe, __, format_currency */

/**
 * Project Brief
 * -------------
 * Adds a "Project Brief" button to the Project form. Clicking it opens a
 * read-only interface laid out like Sapphire's printed Project Brief template
 * (project info, description, contacts, contract terms, payment process),
 * pre-filled from the Project's existing fields. A Print button produces a
 * clean, paper-style printout of the same brief.
 *
 * This is display-only: nothing is saved. Template fields that have no source
 * in the system (PM, Tech Lead, contract type, fee/contingency, etc.) render
 * as blank slots so the brief works as a fillable form, matching the original.
 */

frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Project Brief"), function () {
			open_project_brief(frm);
		});
	},
});

function open_project_brief(frm) {
	frappe.call({
		method: "erpnext_enhancements.project_enhancements.doctype.project.project.get_project_brief_data",
		args: { project_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Building Project Brief..."),
		callback: function (r) {
			if (!r.message) {
				frappe.msgprint(__("Could not load Project Brief data."));
				return;
			}

			const html = build_brief_html(r.message);

			const dialog = new frappe.ui.Dialog({
				title: __("Project Brief"),
				size: "extra-large",
				fields: [{ fieldtype: "HTML", fieldname: "brief", options: html }],
				primary_action_label: __("Print"),
				primary_action: function () {
					print_brief(html);
				},
			});

			dialog.show();
		},
	});
}

/** Format a value for display, falling back to a blank underline slot. */
function slot(value) {
	if (value === null || value === undefined || value === "") {
		return '<span class="sf-blank"></span>';
	}
	return frappe.utils.escape_html(String(value));
}

function fmt_date(value) {
	if (!value) {
		return '<span class="sf-blank"></span>';
	}
	return frappe.datetime.str_to_user(value);
}

function fmt_money(value) {
	if (!value) {
		return '<span class="sf-blank"></span>';
	}
	return format_currency(value);
}

/** An unchecked / checked box matching the printed template. */
function checkbox(label, checked) {
	const mark = checked ? "&#9632;" : "&#9633;"; // filled vs empty square
	return `<span class="sf-check">${mark} ${frappe.utils.escape_html(label)}</span>`;
}

function build_brief_html(d) {
	const address = (d.address_lines && d.address_lines.length)
		? d.address_lines.map((l) => frappe.utils.escape_html(l)).join("<br>")
		: '<span class="sf-blank"></span>';

	const description = d.description
		? frappe.utils.escape_html(d.description).replace(/\n/g, "<br>")
		: '<span class="sf-blank-line"></span>';

	return `
<div class="sf-brief">
	${brief_styles()}

	<!-- Header -->
	<div class="sf-header">
		<div class="sf-logo">Sapphire Fountains<span class="sf-dot">.</span></div>
		<div class="sf-prj">
			<div class="sf-label">Project Number</div>
			<div class="sf-prj-num">${slot(d.project_number)}</div>
		</div>
		<div class="sf-title">
			<div class="sf-brief-title">Project Brief</div>
			<div class="sf-date"><span class="sf-label">Date</span> ${fmt_date(d.brief_date)}</div>
		</div>
	</div>

	<!-- Project info -->
	<div class="sf-grid sf-grid-3 sf-project-block">
		<div>
			<div class="sf-label">Project</div>
			<div class="sf-project-name">${slot(d.project_title)}</div>
			<div class="sf-label sf-mt">Address</div>
			<div class="sf-address">${address}</div>
		</div>
		<div>
			<div class="sf-label">Contract Value</div>
			<div class="sf-value">${fmt_money(d.contract_value)}</div>
			<div class="sf-label sf-mt">Start Date</div>
			<div class="sf-value">${fmt_date(d.start_date)}</div>
			<div class="sf-label sf-mt">Completion Date</div>
			<div class="sf-value">${fmt_date(d.completion_date)}</div>
		</div>
		<div>
			<div class="sf-kv"><span class="sf-label-inline">PM:</span> ${slot(d.pm)}</div>
			<div class="sf-kv"><span class="sf-label-inline">Tech Lead:</span> ${slot(d.tech_lead)}</div>
			<div class="sf-kv sf-mt"><span class="sf-label-inline">Kick-off Meeting</span><br>Date completed: ${fmt_date(d.kickoff_meeting_date)}</div>
			<div class="sf-kv sf-mt"><span class="sf-label-inline">Preliminary Lien Notice</span><br>Date Filed: ${fmt_date(d.prelim_lien_notice_date)}</div>
		</div>
	</div>

	<!-- Description -->
	<div class="sf-section">
		<div class="sf-section-title">Description</div>
		<div class="sf-description">${description}</div>
	</div>

	<!-- Contacts -->
	<div class="sf-section">
		<div class="sf-section-title">Contacts</div>
		<div class="sf-grid sf-grid-2">
			<div class="sf-kv"><span class="sf-label-inline">Owner:</span> ${slot(d.owner)}</div>
			<div class="sf-kv"><span class="sf-label-inline">Contact:</span> ${slot(d.owner_contact)}</div>
			<div class="sf-kv"><span class="sf-label-inline">General Contractor:</span> ${slot(d.general_contractor)}</div>
			<div class="sf-kv"><span class="sf-label-inline">Contact:</span> ${slot(d.gc_contact)}</div>
		</div>
	</div>

	<!-- Contract -->
	<div class="sf-section">
		<div class="sf-section-title">Contract</div>
		<div class="sf-grid sf-grid-2">
			<div>
				<div class="sf-checks">
					${checkbox("Lump Sum", false)}
					${checkbox("Design-Build", false)}
					${checkbox("Cost Plus", false)}
				</div>
				<div class="sf-kv sf-mt"><span class="sf-label-inline">Contract Amount:</span> ${fmt_money(d.contract_amount)}</div>
				<div class="sf-kv"><span class="sf-label-inline">Fee:</span> <span class="sf-blank-sm"></span> % | $ <span class="sf-blank-sm"></span></div>
				<div class="sf-kv"><span class="sf-label-inline">Contingency:</span> <span class="sf-blank-sm"></span> % | $ <span class="sf-blank-sm"></span></div>
				<div class="sf-kv"><span class="sf-label-inline">Risk Reserve:</span> <span class="sf-blank-sm"></span> % | $ <span class="sf-blank-sm"></span></div>
				<div class="sf-kv"><span class="sf-label-inline">Interest on Balances:</span> <span class="sf-blank-sm"></span> % / month</div>
			</div>
			<div>
				<div class="sf-label">Changes</div>
				<div class="sf-note-small">(include fee, general contractor's, subcontractor, and materials costs, as well as any schedule impacts.)</div>
				<div class="sf-blank-line"></div>
				<div class="sf-blank-line"></div>
				<div class="sf-blank-line"></div>
			</div>
		</div>
	</div>

	<!-- Payment process -->
	<div class="sf-section">
		<div class="sf-section-title">Payment Process</div>
		<div class="sf-payment">
			Payment requests are due to the owner on the <strong>Last Day of each Month</strong>.
			Subcontractor and Supplier invoices are due to Sapphire Fountains by the <strong>25th of each month</strong>
			(projecting expenses to the end of the month).
		</div>
		<div class="sf-grid sf-grid-2 sf-mt">
			<div>
				<div class="sf-label">Deliver to</div>
				<div class="sf-blank-line"></div>
			</div>
			<div>
				<div class="sf-label">Deliver via</div>
				<div class="sf-checks">
					${checkbox("email", false)}
					${checkbox("Hand Deliver", false)}
				</div>
				<div class="sf-checks">
					${checkbox("Overnight Service", false)}
					${checkbox("Regular Mail", false)}
				</div>
			</div>
		</div>
	</div>
</div>`;
}

function brief_styles() {
	return `<style>
.sf-brief { color: #1a1a1a; font-family: Arial, Helvetica, sans-serif; font-size: 13px; line-height: 1.4; background: #fff; padding: 10px 6px; }
.sf-brief .sf-label { font-style: italic; color: #666; font-size: 11px; }
.sf-brief .sf-label-inline { font-style: italic; color: #444; font-weight: 600; }
.sf-brief .sf-mt { margin-top: 10px; }
.sf-brief .sf-header { display: flex; align-items: flex-start; justify-content: space-between; border-bottom: 2px solid #1a1a1a; padding-bottom: 14px; margin-bottom: 16px; }
.sf-brief .sf-logo { font-size: 30px; font-weight: 800; color: #2f6fb0; letter-spacing: -1px; }
.sf-brief .sf-dot { color: #2f6fb0; }
.sf-brief .sf-prj { text-align: center; }
.sf-brief .sf-prj-num { font-size: 18px; font-weight: 700; }
.sf-brief .sf-title { text-align: right; }
.sf-brief .sf-brief-title { font-size: 26px; font-weight: 800; font-style: italic; }
.sf-brief .sf-date { margin-top: 8px; font-size: 14px; font-weight: 700; }
.sf-brief .sf-grid { display: grid; gap: 16px; }
.sf-brief .sf-grid-2 { grid-template-columns: 1fr 1fr; }
.sf-brief .sf-grid-3 { grid-template-columns: 1.2fr 1fr 1fr; }
.sf-brief .sf-project-name { font-size: 22px; font-weight: 700; margin-top: 2px; }
.sf-brief .sf-address { margin-top: 2px; }
.sf-brief .sf-value { font-weight: 600; }
.sf-brief .sf-kv { margin-bottom: 4px; }
.sf-brief .sf-section { border-top: 1px solid #c8c8c8; margin-top: 18px; padding-top: 10px; }
.sf-brief .sf-section-title { font-size: 16px; font-weight: 800; font-style: italic; text-transform: uppercase; margin-bottom: 8px; }
.sf-brief .sf-description { white-space: pre-wrap; min-height: 40px; }
.sf-brief .sf-checks { display: flex; gap: 24px; margin-bottom: 6px; }
.sf-brief .sf-check { font-size: 14px; }
.sf-brief .sf-note-small { font-style: italic; color: #777; font-size: 11px; margin: 2px 0 6px; }
.sf-brief .sf-payment { font-size: 13px; }
.sf-brief .sf-blank { display: inline-block; min-width: 90px; border-bottom: 1px solid #999; height: 1em; vertical-align: bottom; }
.sf-brief .sf-blank-sm { display: inline-block; min-width: 36px; border-bottom: 1px solid #999; height: 1em; vertical-align: bottom; }
.sf-brief .sf-blank-line { display: block; border-bottom: 1px solid #bbb; height: 1.6em; margin-bottom: 6px; }
</style>`;
}

function print_brief(html) {
	const win = window.open("", "_blank", "width=900,height=1100");
	if (!win) {
		frappe.msgprint(__("Please allow pop-ups to print the Project Brief."));
		return;
	}
	win.document.write(
		`<!doctype html><html><head><title>${__("Project Brief")}</title>` +
			`<style>@page { margin: 18mm; } body { margin: 0; }</style></head>` +
			`<body onload="window.print();">${html}</body></html>`
	);
	win.document.close();
}
