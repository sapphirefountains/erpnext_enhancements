/**
 * Sapphire Maintenance Template form script.
 *
 * "Preview a day" (v1.678.0): a line can be set to weekdays (Section Item "On Days", v1.619.0),
 * so a template no longer reads as the checklist every visit gets. Pick a weekday and see what a
 * visit on it gets, section by section, with the lines set to other days marked off.
 */
frappe.ui.form.on("Sapphire Maintenance Template", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Preview a day"), () => ee_preview_day(frm));
	},
});

const EE_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function ee_preview_day(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("What a visit gets on a given day"),
		size: "large",
		fields: [
			{
				fieldname: "day",
				fieldtype: "Select",
				label: __("Day"),
				options: EE_DAYS.join("\n"),
				// getDay() counts from Sunday; the list starts on Monday.
				default: EE_DAYS[(new Date().getDay() + 6) % 7],
			},
			{ fieldname: "preview", fieldtype: "HTML" },
		],
	});
	const show = () =>
		frappe
			.call({
				method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_template.sapphire_maintenance_template.preview_day",
				args: { template: frm.doc.name, day: dialog.get_value("day") },
			})
			.then((r) => dialog.fields_dict.preview.$wrapper.html(ee_preview_html(r.message || {}, frm.is_dirty())));
	dialog.fields_dict.day.$input.on("change", show);
	dialog.show();
	show();
}

function ee_preview_html(preview, unsaved) {
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	let html = "";
	if (unsaved) {
		html += `<p class="text-warning">${esc(__("This shows the saved template. Save to preview your changes."))}</p>`;
	}
	html += `<p>${esc(
		__("{0} lines on {1}; {2} set for other days.", [preview.on || 0, preview.day || "", preview.off || 0])
	)}${preview.site_wide ? ` ${esc(__("Filled in once per site visit."))}` : ""}</p>`;
	(preview.sections || []).forEach((section) => {
		html += `<h6 style="margin:14px 0 6px">${esc(section.title)} <span class="text-muted">· ${esc(section.type)}</span></h6>`;
		html += '<ul style="margin:0 0 4px 18px;padding:0">';
		(section.lines || []).forEach((line) => {
			const days = line.days ? ` <span class="text-muted">(${esc(line.days)})</span>` : "";
			const required = line.mandatory ? ` <span class="text-muted">· ${esc(__("required"))}</span>` : "";
			html += line.on
				? `<li>${esc(line.label)}${days}${required}</li>`
				: `<li class="text-muted"><s>${esc(line.label)}</s>${days}</li>`;
		});
		html += "</ul>";
	});
	return html;
}
