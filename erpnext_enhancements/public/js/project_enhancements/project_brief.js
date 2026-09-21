/**
 * Project Brief
 * -------------
 * Targets: the Project DocType form.
 * Loaded via: hooks.py `doctype_js["Project"]`.
 *
 * Adds a "Project Brief" button to the Project form. Clicking it opens a
 * read-only interface laid out like Sapphire's printed Project Brief template
 * (project info, description, contacts, contract terms, payment process),
 * pre-filled from the Project's existing fields. A Print button produces a
 * clean, paper-style printout of the same brief.
 *
 * This is display-only: nothing is saved. Template fields that have no source
 * in the system (PM, Tech Lead, contract type, fee/contingency, etc.) render
 * as blank slots so the brief works as a fillable form, matching the original.
 *
 * Everything above is the half of the brief that is the same for every job. The
 * other half arrives in `sections` -- one per line of work the job involves
 * (Design, Build, Products, Service, Events), each a list of blocks in one of
 * four shapes. `render_block` is the whole vocabulary:
 *
 *   fields  label/value lines, each optionally with a note. Rendered even when
 *           every value is empty -- an Events sheet with four blank date slots
 *           is the sheet somebody writes the setup time onto.
 *   list    bulleted lines (customer requests, deliverables).
 *   text    a paragraph (scope of work, scheduling notes).
 *   table   a grid, with a footnote when the server truncated it.
 *
 * The server sends values unformatted with a `format` name beside them, so a
 * date renders in the reader's own date format and a currency in the site's,
 * rather than in whatever the server picked. Which section a job gets, and why
 * it can be more than one, is decided server-side --
 * `project_enhancements/project_brief.py` has the reasoning.
 */

/*
 * The print design system's chrome (docs/print-design-system.md), rendered in the
 * browser. These four records are a COPY of print_style.PILLARS — the brief is
 * built client-side and cannot read the Python module, and
 * tests/test_print_style.py holds the two in agreement. The pillar is the job's
 * leading stream; a job outside the five streams takes the brand's dark band.
 *
 * Raw /assets paths, deliberately: both files are immutable by content (the
 * wordmark SVG the contracts inline, the design system's woff2), so the 1-year
 * immutable cache on raw asset paths cannot serve a stale one.
 */
const PILLARS = {
	design: { name: "DESIGN", open: "#b14fc5", end: "#55265f", deep: "#55265f" },
	build: { name: "BUILD", open: "#00609c", end: "#002136", deep: "#002136" },
	service: { name: "SERVICE", open: "#00a0df", end: "#005779", deep: "#005779" },
	rent: { name: "RENT", open: "#62cbc9", end: "#316564", deep: "#316564" },
};
const NEUTRAL_PILLAR = { name: "", open: "#00263e", end: "#00111c", deep: "#00609c" };
const STREAM_PILLAR = { Design: "design", Build: "build", Products: "build", Service: "service", Events: "rent" };
const LOGO_URL = window.location.origin + "/assets/erpnext_enhancements/images/fountain_move/logo.svg";
const FONT_URL = window.location.origin + "/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2";

/** The pillar the sheet is coloured for: the leading stream's, else neutral. */
function pillar_for(d) {
	const lead = (d.work_streams && d.work_streams.length ? d.work_streams : [d.project_stage]).filter(
		Boolean
	)[0];
	return PILLARS[STREAM_PILLAR[lead]] || NEUTRAL_PILLAR;
}

frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (frm.is_new()) {
			return;
		}

		// Grouped under ERPNext's native "View" dropdown (Gantt Chart / Kanban
		// Board): the brief is a read-only viewer, so it belongs with the other
		// ways of looking at the project rather than as its own toolbar button.
		frm.add_custom_button(
			__("Project Brief"),
			function () {
				open_project_brief(frm);
			},
			__("View")
		);
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

/** A datetime; `str_to_user` keeps the time whenever the string carries one. */
function fmt_datetime(value) {
	if (!value) {
		return '<span class="sf-blank"></span>';
	}
	return frappe.datetime.str_to_user(value);
}

function fmt_percent(value) {
	// 0% is a fact here (nothing received yet), not a missing value, so only
	// null/undefined/"" falls through to the blank slot.
	if (value === null || value === undefined || value === "") {
		return '<span class="sf-blank"></span>';
	}
	return frappe.utils.escape_html(String(Math.round(Number(value) || 0))) + "%";
}

/** The `format` names the server sends, mapped to the renderer for each. */
const FORMATTERS = {
	text: slot,
	date: fmt_date,
	datetime: fmt_datetime,
	currency: fmt_money,
	percent: fmt_percent,
};

function fmt_value(value, format_name) {
	return (FORMATTERS[format_name] || slot)(value);
}

/** An unchecked / checked box matching the printed template. */
function checkbox(label, checked) {
	const mark = checked ? "&#9632;" : "&#9633;"; // filled vs empty square
	return `<span class="sf-check">${mark} ${frappe.utils.escape_html(label)}</span>`;
}

/** A label/value block: the fillable skeleton, blanks included. */
function render_fields_block(block) {
	const rows = (block.rows || [])
		.map(function (row) {
			// Newlines survive as line breaks: the delivery note on a real Events
			// job is a shipping address typed over three lines, and flattened to
			// one it is a paragraph nobody reads off a clipboard.
			const note = row.note
				? `<div class="sf-row-note">${frappe.utils
						.escape_html(String(row.note))
						.replace(/\n/g, "<br>")}</div>`
				: "";
			return `<div class="sf-row">
				<div class="sf-row-label">${frappe.utils.escape_html(row.label || "")}</div>
				<div class="sf-row-value">${fmt_value(row.value, row.format)}${note}</div>
			</div>`;
		})
		.join("");
	return `<div class="sf-block"><div class="sf-block-title">${frappe.utils.escape_html(
		block.title || ""
	)}</div><div class="sf-rows">${rows}</div></div>`;
}

function render_list_block(block) {
	const items = (block.items || [])
		.map((item) => `<li>${frappe.utils.escape_html(String(item)).replace(/\n/g, "<br>")}</li>`)
		.join("");
	return `<div class="sf-block"><div class="sf-block-title">${frappe.utils.escape_html(
		block.title || ""
	)}</div><ul class="sf-bullets">${items}</ul></div>`;
}

function render_text_block(block) {
	const text = frappe.utils.escape_html(String(block.text || "")).replace(/\n/g, "<br>");
	return `<div class="sf-block"><div class="sf-block-title">${frappe.utils.escape_html(
		block.title || ""
	)}</div><div class="sf-block-text">${text}</div></div>`;
}

function render_table_block(block) {
	const columns = block.columns || [];
	const head = columns
		.map((col) => `<th>${frappe.utils.escape_html(col.label || "")}</th>`)
		.join("");
	const body = (block.rows || [])
		.map(function (row) {
			const cells = columns
				.map((col, index) => `<td>${fmt_value(row[index], col.format)}</td>`)
				.join("");
			return `<tr>${cells}</tr>`;
		})
		.join("");
	// The server caps long lists; say so rather than letting a partial sheet read
	// as the whole list.
	const more = block.truncated
		? `<div class="sf-note-small">${__("and {0} more", [block.truncated])}</div>`
		: "";
	return `<div class="sf-block sf-block-wide"><div class="sf-block-title">${frappe.utils.escape_html(
		block.title || ""
	)}</div><table class="sf-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${more}</div>`;
}

const BLOCK_RENDERERS = {
	fields: render_fields_block,
	list: render_list_block,
	text: render_text_block,
	table: render_table_block,
};

function render_block(block) {
	const renderer = BLOCK_RENDERERS[block && block.kind];
	return renderer ? renderer(block) : "";
}

/** One "<Stream> Details" section per line of work the job involves. */
function build_sections_html(sections) {
	if (!sections || !sections.length) {
		return "";
	}
	return sections
		.map(function (section) {
			const blocks = (section.blocks || []).map(render_block).join("");
			return `<div class="sf-section sf-type-section">
				<div class="sf-section-title">${frappe.utils.escape_html(section.title || "")} ${__("Details")}</div>
				<div class="sf-blocks">${blocks}</div>
			</div>`;
		})
		.join("");
}

function build_brief_html(d) {
	const address = (d.address_lines && d.address_lines.length)
		? d.address_lines.map((l) => frappe.utils.escape_html(l)).join("<br>")
		: '<span class="sf-blank"></span>';

	// What kind of job this is, on the sheet. Falls back to the stage alone when
	// the job is one the type-specific sections do not cover (Internal, Overhead).
	const streams = (d.work_streams && d.work_streams.length ? d.work_streams : [d.project_stage])
		.filter(Boolean)
		.map((s) => frappe.utils.escape_html(String(s)))
		.join(" &middot; ");

	const description = d.description
		? frappe.utils.escape_html(d.description).replace(/\n/g, "<br>")
		: '<span class="sf-blank-line"></span>';

	const pillar = pillar_for(d);

	return `
<div class="sf-brief">
	${brief_styles()}

	<!-- Header: the print design system's letterhead — the pillar stripe, the
	     wordmark, an eyebrow naming the pillar over the display-face title. -->
	<div class="sf-stripe" style="background-color:${pillar.open}; background-image:linear-gradient(90deg, ${pillar.open} 0%, ${pillar.end} 100%);"></div>
	<div class="sf-header">
		<div class="sf-logo"><img src="${LOGO_URL}" alt="Sapphire Fountains" width="170" height="62"></div>
		<div class="sf-title">
			<div class="sf-eyebrow" style="color:${pillar.deep};">${pillar.name ? pillar.name + " &middot; " : ""}PROJECT BRIEF</div>
			<div class="sf-brief-title">Project Brief</div>
			<div class="sf-prj-num">${slot(d.project_number)} &middot; ${fmt_date(d.brief_date)}</div>
			<div class="sf-streams">${streams || '<span class="sf-blank"></span>'}</div>
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

	<!-- What this job actually is: one section per line of work it involves.
	     Placed with the description rather than after the contract terms,
	     because it is the continuation of "what are we doing here". Empty for
	     an Internal or untyped job, which leaves the sheet as it always was. -->
	${build_sections_html(d.sections)}

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

/*
 * The print design system's look, in the design system's tokens: a white sheet
 * (drawn under the brief even in a dark desk theme, like the contract viewer),
 * ink-700 Lato, bahama-blue labels and display-face section titles, rules and
 * weight for separation — no tints, no dotted rules, no italics-as-hierarchy.
 * The two brand colours that carry small text are the closing stops; the
 * opening stops paint only the stripe.
 */
function brief_styles() {
	return `<style>
@font-face { font-family: "Big Noodle Titling"; font-weight: 700; font-style: normal; src: url("${FONT_URL}") format("woff2"); }
.sf-brief { background: #fff; color: #363636; font-family: Lato, "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 12.5px; line-height: 1.4; padding: 0 0 8px; }
.sf-brief .sf-stripe { height: 10px; margin-bottom: 16px; }
.sf-brief .sf-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 12px; }
.sf-brief .sf-logo img { display: block; width: 170px; height: auto; }
.sf-brief .sf-title { text-align: right; }
.sf-brief .sf-eyebrow { font-size: 11.5px; font-weight: 700; }
.sf-brief .sf-brief-title { font-family: "Big Noodle Titling", "Arial Narrow", Arial, sans-serif; font-weight: 700; font-size: 38px; line-height: 1; color: #00263e; margin-top: 4px; }
.sf-brief .sf-prj-num { font-weight: 700; color: #151515; margin-top: 6px; }
.sf-brief .sf-streams { font-size: 11.5px; font-weight: 700; color: #00609c; margin-top: 2px; }
.sf-brief .sf-label { font-size: 11.5px; font-weight: 700; color: #00609c; text-transform: uppercase; }
.sf-brief .sf-label-inline { font-weight: 700; color: #00609c; }
.sf-brief .sf-mt { margin-top: 10px; }
.sf-brief .sf-grid { display: grid; gap: 16px 24px; }
.sf-brief .sf-grid-2 { grid-template-columns: 1fr 1fr; }
.sf-brief .sf-grid-3 { grid-template-columns: 1.2fr 1fr 1fr; }
.sf-brief .sf-project-block { border-top: 1px solid #00263e; border-bottom: 1px solid #dadbdd; padding: 10px 0; }
.sf-brief .sf-project-name { font-size: 16px; font-weight: 700; color: #151515; margin-top: 2px; }
.sf-brief .sf-address { margin-top: 2px; }
.sf-brief .sf-value { font-weight: 700; color: #151515; }
.sf-brief .sf-kv { margin-bottom: 4px; }
.sf-brief .sf-section { margin-top: 16px; }
.sf-brief .sf-section-title { font-family: "Big Noodle Titling", "Arial Narrow", Arial, sans-serif; font-weight: 700; font-size: 20px; line-height: 1; color: #00609c; margin-bottom: 8px; }
.sf-brief .sf-description { white-space: pre-wrap; min-height: 40px; }
.sf-brief .sf-checks { display: flex; gap: 24px; margin-bottom: 6px; }
.sf-brief .sf-check { font-size: 13px; }
.sf-brief .sf-note-small { font-size: 11px; margin: 2px 0 6px; }
.sf-brief .sf-payment { font-size: 12.5px; }
.sf-brief .sf-blank { display: inline-block; min-width: 90px; border-bottom: 1px solid #00263e; height: 1em; vertical-align: bottom; }
.sf-brief .sf-blank-sm { display: inline-block; min-width: 36px; border-bottom: 1px solid #00263e; height: 1em; vertical-align: bottom; }
.sf-brief .sf-blank-line { display: block; border-bottom: 1px solid #00263e; height: 1.6em; margin-bottom: 6px; }
/* Blocks flow in two columns and reflow to one when the dialog is narrow; a
   table block always claims the full width, since six columns in half of one
   is unreadable. */
.sf-brief .sf-blocks { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 24px; align-items: start; }
.sf-brief .sf-block-wide { grid-column: 1 / -1; }
.sf-brief .sf-block-title { font-weight: 700; font-size: 11.5px; text-transform: uppercase; color: #00609c; margin-bottom: 4px; }
.sf-brief .sf-row { display: flex; gap: 8px; align-items: baseline; padding: 3px 0; border-bottom: 1px solid #dadbdd; }
.sf-brief .sf-row-label { flex: 0 0 46%; color: #363636; }
.sf-brief .sf-row-value { flex: 1 1 auto; font-weight: 700; color: #151515; min-width: 0; }
.sf-brief .sf-row-note { font-weight: 400; font-size: 11px; color: #363636; }
.sf-brief .sf-bullets { margin: 0; padding-left: 18px; }
.sf-brief .sf-bullets li { margin-bottom: 3px; }
.sf-brief .sf-block-text { white-space: pre-wrap; }
.sf-brief .sf-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.sf-brief .sf-table th { text-align: left; font-weight: 700; font-size: 11.5px; color: #00609c; border-bottom: 2px solid #00263e; padding: 4px 6px 4px 0; }
.sf-brief .sf-table td { padding: 4px 6px 4px 0; border-bottom: 1px solid #dadbdd; vertical-align: top; }
@media (max-width: 720px) {
  .sf-brief .sf-blocks { grid-template-columns: 1fr; }
  .sf-brief .sf-grid-2, .sf-brief .sf-grid-3 { grid-template-columns: 1fr; }
}
@media print {
  /* The stripe is a background; browsers drop backgrounds when printing unless
     told not to, and a brief without its stripe is a brief without its pillar. */
  .sf-brief .sf-stripe { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  /* Keep a section whole on one page where it fits: a rental fee schedule split
     across a page break is how a number gets read against the wrong heading. */
  .sf-brief .sf-type-section { break-inside: avoid; page-break-inside: avoid; }
  .sf-brief .sf-block { break-inside: avoid; page-break-inside: avoid; }
}
</style>`;
}

function print_brief(html) {
	const win = window.open("", "_blank", "width=900,height=1100");
	if (!win) {
		frappe.msgprint(__("Please allow pop-ups to print the Project Brief."));
		return;
	}
	// Print once the display face has arrived: window.print() on load fires
	// before a webfont does, and the title would go out in Arial Narrow.
	win.document.write(
		`<!doctype html><html><head><meta charset="utf-8"><title>${__("Project Brief")}</title>` +
			`<style>@page { margin: 15mm; } body { margin: 0; background: #fff; }</style></head>` +
			`<body onload="document.fonts.ready.then(function () { window.print(); });">${html}</body></html>`
	);
	win.document.close();
}
