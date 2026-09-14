// Inspection Wizard — the inspector's touch-first, section-at-a-time inspection form.
//
// A desk Page (/app/inspection-wizard?inspection=QIR-...) that walks a
// Project Quality Inspection one frozen section at a time. Every answer is a tap; a
// measurement is one numeric field with its contracted range printed beside it. Without
// ?inspection= it lists the drafts assigned to the signed-in user.
//
// It reads and writes the same record as the desk form, through api/quality_wizard.py:
// bootstrap once, autosave a field-allowlisted patch with optimistic locking, submit
// server-side. All downstream automation — non-conformances, carried-fix verdicts, the
// Critical alert — hangs off on_submit and is untouched. The desk form stays the
// supervisor surface.
//
// THE ROWS ARE FROZEN AND THIS PAGE CANNOT CHANGE THEM. The check text, the acceptance
// criteria and the measurement bounds were copied from the master template at generation.
// They are rendered here and never sent back; the server's allowlist accepts only outcome,
// measured_value, notes and photo. A wizard that could edit a bound could turn a failing
// measurement into a passing one from a phone, on site, with nothing in the diff.
//
// Styling uses Frappe CSS variables so Frappe Light and Timeless Night both work; pass/fail
// colours stay literal, matching the Visit Wizard's house convention. The page loader serves
// this file version-aware, so no .bundle.* is needed.

frappe.pages["inspection-wizard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Inspection Wizard"),
		single_column: true,
	});
	wrapper.inspection_wizard = new InspectionWizard(page, wrapper);
};

frappe.pages["inspection-wizard"].on_page_show = function (wrapper) {
	if (wrapper.inspection_wizard) {
		wrapper.inspection_wizard.handle_route();
	}
};

const QW_STYLE = `
.qw-wrap{max-width:640px;margin:0 auto;padding-bottom:104px;font-size:15px;}
.qw-muted{color:var(--text-muted);font-size:13px;}
.qw-progress{height:6px;border-radius:3px;background:var(--control-bg);margin:8px 0 4px;overflow:hidden;}
.qw-progress>div{height:100%;border-radius:3px;background:var(--primary,#2490ef);transition:width .25s;}
.qw-stepline{display:flex;justify-content:space-between;align-items:center;font-size:14px;color:var(--text-muted);margin-bottom:10px;}
.qw-tabs{display:flex;gap:8px;overflow-x:auto;padding:2px 0 10px;position:sticky;top:0;background:var(--bg-color);z-index:3;}
.qw-tab{flex:0 0 auto;padding:9px 15px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:15px;cursor:pointer;white-space:nowrap;}
.qw-tab.qw-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.qw-tab.qw-has-fail{border-color:#dc2626;}
.qw-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);}
.qw-card.qw-bad{border-color:#dc2626;background:rgba(220,38,38,.06);}
.qw-card.qw-done{border-color:#15803d;}
.qw-card-title{font-weight:600;font-size:17px;line-height:1.35;}
.qw-card-sub{font-size:14px;color:var(--text-muted);margin-top:4px;}
.qw-chip{display:inline-block;font-size:12px;border-radius:10px;padding:2px 9px;margin-left:6px;vertical-align:middle;}
.qw-chip-req{background:#fef3c7;color:#92400e;font-weight:600;}
.qw-chip-photo{background:#e5e7eb;color:#374151;}
.qw-chip-carried{background:#ede9fe;color:#5b21b6;font-weight:600;}
.qw-chip-addendum{background:#dbeafe;color:#1e40af;}
.qw-chip-red{background:#fde8e8;color:#b91c1c;font-weight:600;}
.qw-seg{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;}
.qw-seg button{flex:1 1 calc(33% - 8px);min-width:84px;min-height:48px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;cursor:pointer;padding:7px 4px;}
.qw-seg button.qw-on{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.qw-seg button.qw-on.qw-neg{background:#dc2626;border-color:#dc2626;}
.qw-seg button.qw-on.qw-na{background:#6b7280;border-color:#6b7280;}
.qw-measure{display:flex;gap:10px;align-items:center;margin-top:12px;}
.qw-measure input{flex:1;font-size:24px;text-align:center;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.qw-measure input:focus{outline:2px solid var(--primary,#2490ef);}
.qw-measure input.qw-oor{border-color:#dc2626;color:#b91c1c;font-weight:700;}
.qw-measure .qw-uom{flex:0 0 auto;font-size:15px;color:var(--text-muted);}
.qw-range{margin-top:6px;font-size:13px;color:var(--text-muted);text-align:center;}
.qw-range.qw-oor{color:#b91c1c;font-weight:600;}
.qw-row-extra{margin-top:10px;display:flex;gap:8px;align-items:center;}
.qw-row-extra input{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.qw-photo-btn{flex:0 0 auto;min-width:52px;min-height:44px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);cursor:pointer;font-size:18px;}
.qw-photo-btn.qw-has-photo{border-color:#15803d;background:#e7f7ed;}
.qw-banner{border-radius:10px;padding:12px 14px;margin-bottom:10px;font-size:15px;line-height:1.45;}
.qw-banner-red{background:#fde8e8;color:#b91c1c;}
.qw-banner-blue{background:rgba(36,144,239,.1);color:var(--text-color);}
.qw-banner-green{background:#e7f7ed;color:#15803d;}
.qw-banner-amber{background:#fef3c7;color:#92400e;}
.qw-banner h6{margin:0 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;}
.qw-help{border:1px solid var(--border-color);border-radius:10px;margin-bottom:10px;overflow:hidden;background:var(--card-bg);}
.qw-help-head{display:flex;align-items:center;gap:8px;width:100%;padding:12px 14px;background:none;border:none;color:var(--text-color);font-size:15px;font-weight:600;cursor:pointer;text-align:left;}
.qw-help-caret{margin-left:auto;transition:transform .15s;}
.qw-help.qw-open .qw-help-caret{transform:rotate(90deg);}
.qw-help-body{padding:0 14px 14px;font-size:14px;line-height:1.5;color:var(--text-color);}
.qw-nav{position:fixed;left:0;right:0;bottom:0;display:flex;gap:10px;padding:12px 14px calc(12px + env(safe-area-inset-bottom));background:var(--bg-color);border-top:1px solid var(--border-color);z-index:5;}
.qw-nav button{flex:1;min-height:50px;border-radius:9px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:16px;cursor:pointer;}
.qw-nav button.qw-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.qw-nav button:disabled{opacity:.5;cursor:default;}
.qw-savestate{position:fixed;left:0;right:0;bottom:calc(74px + env(safe-area-inset-bottom));text-align:center;font-size:13px;pointer-events:none;z-index:6;}
.qw-savestate span{display:inline-block;padding:4px 12px;border-radius:12px;background:var(--control-bg);color:var(--text-muted);border:1px solid var(--border-color);}
.qw-savestate.qw-err span{background:#fde8e8;color:#b91c1c;border-color:#b91c1c;font-weight:600;pointer-events:auto;}
.qw-list-item{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);cursor:pointer;}
.qw-list-item h5{margin:0 0 4px;font-size:16px;}
.qw-sign{margin-top:10px;border:1px solid var(--border-color);border-radius:10px;padding:12px;background:var(--card-bg);}
.qw-sign-img{max-width:100%;border:1px solid var(--border-color);border-radius:8px;background:#fff;}
.qw-empty{text-align:center;padding:40px 16px;color:var(--text-muted);}
`;

// ---------------------------------------------------------------------------
// Rendering authored text.
//
// Two helpers and never frappe.utils.xss_sanitise, whose default strategies are
// ["html", "js"] — it escapes <, >, ", ' and /, so a Text Editor field renders as visible
// tags and the inspector reads a literal <ul><li><b> instead of a list. The panel still
// LOOKS populated, which is exactly why that shipped once on the maintenance side and
// survived review. tests/test_inspection_wizard_markup.py fails the build on it now.
// ---------------------------------------------------------------------------

function qw_rich_html(html) {
	// Text Editor content: keep the markup, drop anything that could execute.
	if (!html) return "";
	const doc = new DOMParser().parseFromString(String(html), "text/html");
	doc.querySelectorAll("script, style, iframe, object, embed, link, meta").forEach((node) =>
		node.remove()
	);
	doc.querySelectorAll("*").forEach((node) => {
		Array.from(node.attributes).forEach((attr) => {
			const name = attr.name.toLowerCase();
			const value = (attr.value || "").trim().toLowerCase();
			if (name.startsWith("on") || value.startsWith("javascript:")) {
				node.removeAttribute(attr.name);
			}
		});
	});
	return doc.body.innerHTML;
}

function qw_plain_html(text) {
	// Small Text content: one item per line, so collapsing the newlines hides one.
	if (!text) return "";
	return frappe.utils.escape_html(String(text)).replace(/\n/g, "<br>");
}

// Which answers count as a failure. Mirrors quality/merge.py FAILING_ANSWERS; an out-of-range
// measurement is a failure too, and the server is the one that decides that — the wizard shows
// what came back rather than re-deriving it, so the two can never disagree on screen.
const QW_FAILING = ["Fail"];

class InspectionWizard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.body = $('<div class="qw-wrap"></div>').appendTo(page.main);
		$(`<style>${QW_STYLE}</style>`).appendTo(page.main);
		this.section_index = 0;
		this.pending = { fields: {}, rows: {} };
		this.saving = false;
		this.retry_timer = null;
		this.bind_unload();
		this.handle_route();
	}

	bind_unload() {
		// A closed tab with an unsaved answer is a check somebody will swear they did.
		$(window).on("beforeunload.qw", () => {
			if (this.has_pending() || this.saving) {
				return __("Some answers have not saved yet.");
			}
		});
	}

	has_pending() {
		return Object.keys(this.pending.fields).length > 0 || Object.keys(this.pending.rows).length > 0;
	}

	handle_route() {
		const name = frappe.utils.get_url_arg("inspection");
		if (!name) {
			this.render_list();
			return;
		}
		if (this.doc && this.doc.header.name === name) return;
		this.load(name);
	}

	// ------------------------------------------------------------------ loading

	load(name) {
		this.body.html(`<div class="qw-empty">${__("Loading...")}</div>`);
		frappe.call({
			method: "erpnext_enhancements.api.quality_wizard.get_inspection_bootstrap",
			args: { inspection: name },
			callback: (r) => {
				if (!r || !r.message) return;
				this.doc = r.message;
				this.section_index = 0;
				this.render();
			},
		});
	}

	render_list() {
		this.doc = null;
		this.body.html(`<div class="qw-empty">${__("Loading...")}</div>`);
		frappe.call({
			method: "erpnext_enhancements.api.quality_wizard.get_open_inspections",
			callback: (r) => {
				const data = (r && r.message) || {};
				const rows = data.inspections || [];
				if (!rows.length) {
					this.body.html(
						`<div class="qw-empty"><p>${__("No inspections are assigned to you.")}</p>
						 <p class="qw-muted">${__("An inspection is generated from a project milestone. Ask your project manager, or open the Quality Control workspace.")}</p></div>`
					);
					return;
				}
				const items = rows
					.map((row) => {
						const done = `${row.mandatory_answered || 0}/${row.mandatory_total || 0}`;
						return `<button class="qw-list-item" data-name="${frappe.utils.escape_html(row.name)}">
							<h5>${frappe.utils.escape_html(row.project_name || row.project || row.name)}</h5>
							<div class="qw-muted">${frappe.utils.escape_html(row.milestone || "")}</div>
							<div class="qw-muted">${__("Required checks answered")}: ${done}
							 &middot; ${frappe.utils.escape_html(row.scheduled_date || row.inspection_date || "")}</div>
						</button>`;
					})
					.join("");
				this.body.html(`<h4>${__("Your open inspections")}</h4>${items}`);
				this.body.find(".qw-list-item").on("click", (event) => {
					const name = $(event.currentTarget).data("name");
					frappe.set_route("inspection-wizard", { inspection: name });
					this.load(name);
				});
			},
		});
	}

	// ----------------------------------------------------------------- rendering

	sections() {
		return (this.doc && this.doc.sections) || [];
	}

	current_section() {
		return this.sections()[this.section_index] || { title: "", rows: [] };
	}

	render() {
		if (!this.doc) return;
		const state = this.doc.state;
		if (state.docstatus === 1) {
			this.render_submitted();
			return;
		}
		this.body.empty();
		this.render_header();
		this.render_tabs();
		this.render_section();
		this.render_nav();
		this.render_save_state();
	}

	render_header() {
		const header = this.doc.header;
		const state = this.doc.state;
		const percent = Math.round(state.completion_percent || 0);
		$(`<div>
			<h4 style="margin-bottom:2px;">${frappe.utils.escape_html(header.project_name || header.project || "")}</h4>
			<div class="qw-muted">${frappe.utils.escape_html(header.milestone || "")} &middot;
				${frappe.utils.escape_html(header.name)}</div>
			<div class="qw-progress"><div style="width:${percent}%"></div></div>
			<div class="qw-stepline">
				<span>${__("Required checks")}: ${state.mandatory_answered || 0}/${state.mandatory_total || 0}</span>
				<span>${state.fail_count ? `<span class="qw-chip qw-chip-red">${state.fail_count} ${__("failing")}</span>` : ""}</span>
			</div>
		</div>`).appendTo(this.body);

		if (header.generation_note) {
			// Contracted criteria that named no milestone. Surfaced, never filtered away: a
			// promise that was sold and never inspected is the failure this whole programme
			// exists to end.
			$(`<div class="qw-banner qw-banner-amber"><h6>${__("Note from generation")}</h6>
				${qw_plain_html(header.generation_note)}</div>`).appendTo(this.body);
		}
		this.render_help(__("Safety"), this.doc.instructions.safety, "qw-banner-red");
	}

	render_help(title, html, _tone) {
		if (!html) return;
		const block = $(`<div class="qw-help">
			<button class="qw-help-head">${frappe.utils.escape_html(title)}
				<span class="qw-help-caret">&rsaquo;</span></button>
			<div class="qw-help-body" style="display:none;">${qw_rich_html(html)}</div>
		</div>`).appendTo(this.body);
		block.find(".qw-help-head").on("click", () => {
			block.toggleClass("qw-open");
			block.find(".qw-help-body").toggle();
		});
	}

	render_tabs() {
		const tabs = $('<div class="qw-tabs"></div>').appendTo(this.body);
		this.sections().forEach((section, index) => {
			const failing = section.rows.some(
				(row) => QW_FAILING.indexOf(row.outcome) !== -1 || row.out_of_range
			);
			const classes = [
				"qw-tab",
				index === this.section_index ? "qw-active" : "",
				failing ? "qw-has-fail" : "",
			].join(" ");
			$(`<button class="${classes}">${frappe.utils.escape_html(section.title)}</button>`)
				.appendTo(tabs)
				.on("click", () => {
					this.section_index = index;
					this.render();
				});
		});
	}

	render_section() {
		const section = this.current_section();
		if (section.location_note) {
			$(`<div class="qw-banner qw-banner-blue"><h6>${__("Where")}</h6>
				${qw_plain_html(section.location_note)}</div>`).appendTo(this.body);
		}
		section.rows.forEach((row) => this.render_row(row));
		if (this.section_index === this.sections().length - 1) {
			this.render_help(__("Before you finish"), this.doc.instructions.wrapup, "qw-banner-blue");
			this.render_wrapup();
		}
	}

	render_row(row) {
		const failing = QW_FAILING.indexOf(row.outcome) !== -1 || row.out_of_range;
		const answered = !!(row.outcome || row.measured_value || row.measured_value === 0);
		const classes = ["qw-card", failing ? "qw-bad" : answered ? "qw-done" : ""].join(" ");
		const card = $(`<div class="${classes}"></div>`).appendTo(this.body);

		$(`<div class="qw-card-title">${qw_plain_html(row.label)}${qw_required_chip(row)}</div>`).appendTo(
			card
		);
		if (row.acceptance_criteria) {
			$(`<div class="qw-card-sub"><b>${__("Passes when")}:</b> ${qw_plain_html(row.acceptance_criteria)}</div>`).appendTo(
				card
			);
		}
		if (row.method) {
			$(`<div class="qw-card-sub"><b>${__("How")}:</b> ${qw_plain_html(row.method)}</div>`).appendTo(card);
		}
		if (row.non_conformance) {
			$(`<div class="qw-card-sub"><span class="qw-chip qw-chip-red">${__("Non-conformance raised")}</span>
				${frappe.utils.escape_html(row.non_conformance)}</div>`).appendTo(card);
		}

		if (row.check_type === "Measurement") {
			this.render_measurement(card, row);
		}
		this.render_answers(card, row);
		this.render_extras(card, row);
	}

	render_measurement(card, row) {
		const wrap = $('<div class="qw-measure"></div>').appendTo(card);
		const input = $(
			`<input type="number" inputmode="decimal" step="any" value="${row.measured_value != null ? row.measured_value : ""}">`
		).appendTo(wrap);
		if (row.out_of_range) input.addClass("qw-oor");
		if (row.uom) $(`<span class="qw-uom">${frappe.utils.escape_html(row.uom)}</span>`).appendTo(wrap);

		// The contracted range, printed. An inspector who cannot see the bound is guessing at
		// what "passes" means, which is the gap this whole programme exists to close.
		const range = $(
			`<div class="qw-range ${row.out_of_range ? "qw-oor" : ""}">${__("Range")}: ${qw_range_text(row)}</div>`
		).appendTo(card);
		input.on("change", () => {
			const value = input.val() === "" ? null : parseFloat(input.val());
			row.measured_value = value;
			this.queue_row(row.name, { measured_value: value });
			range.removeClass("qw-oor");
		});
	}

	render_answers(card, row) {
		const options = row.options || [];
		if (!options.length) return;
		const seg = $('<div class="qw-seg"></div>').appendTo(card);
		options.forEach((option) => {
			const negative = QW_FAILING.indexOf(option) !== -1;
			const neutral = option === "N/A";
			const on = row.outcome === option;
			const classes = [
				on ? "qw-on" : "",
				on && negative ? "qw-neg" : "",
				on && neutral ? "qw-na" : "",
			].join(" ");
			$(`<button class="${classes}">${frappe.utils.escape_html(option)}</button>`)
				.appendTo(seg)
				.on("click", () => {
					row.outcome = row.outcome === option ? null : option;
					this.queue_row(row.name, { outcome: row.outcome });
					this.render();
				});
		});
	}

	render_extras(card, row) {
		const wrap = $('<div class="qw-row-extra"></div>').appendTo(card);
		const notes = $(
			`<input type="text" placeholder="${__("Note (optional)")}" value="${frappe.utils.escape_html(row.notes || "")}">`
		).appendTo(wrap);
		notes.on("change", () => {
			row.notes = notes.val();
			this.queue_row(row.name, { notes: row.notes });
		});

		const photo = $(
			`<button class="qw-photo-btn ${row.photo ? "qw-has-photo" : ""}" title="${__("Photo")}">&#128247;</button>`
		).appendTo(wrap);
		photo.on("click", () => this.capture_photo(row, photo));
	}

	capture_photo(row, button) {
		new frappe.ui.FileUploader({
			doctype: "Project Quality Inspection",
			docname: this.doc.header.name,
			frm: null,
			allow_multiple: false,
			restrictions: { allowed_file_types: ["image/*"] },
			on_success: (file) => {
				row.photo = file.file_url;
				button.addClass("qw-has-photo");
				this.queue_row(row.name, { photo: file.file_url });
			},
		});
	}

	render_wrapup() {
		const header = this.doc.header;
		const card = $(`<div class="qw-card"><div class="qw-card-title">${__("Wrap up")}</div></div>`).appendTo(
			this.body
		);

		const date = $(`<div class="qw-row-extra">
			<input type="date" value="${frappe.utils.escape_html(header.inspection_date || frappe.datetime.get_today())}">
		</div>`).appendTo(card);
		date.find("input").on("change", (event) => {
			this.queue_field("inspection_date", $(event.currentTarget).val());
		});

		const remarks = $(
			`<div class="qw-row-extra"><input type="text" placeholder="${__("Remarks (optional)")}" value="${frappe.utils.escape_html(header.remarks || "")}"></div>`
		).appendTo(card);
		remarks.find("input").on("change", (event) => {
			this.queue_field("remarks", $(event.currentTarget).val());
		});

		this.render_signature(card, __("Inspector signature"), "inspector_sign_off");
		this.render_signature(card, __("Client representative (optional)"), "client_sign_off");
	}

	render_signature(card, label, fieldname) {
		const current = this.doc.header[fieldname];
		const block = $(`<div class="qw-sign">
			<div class="qw-muted" style="margin-bottom:6px;">${frappe.utils.escape_html(label)}</div>
			${current ? `<img class="qw-sign-img" src="${frappe.utils.escape_html(current)}">` : ""}
		</div>`).appendTo(card);
		$(`<button class="btn btn-sm btn-default" style="margin-top:8px;">${current ? __("Re-sign") : __("Sign")}</button>`)
			.appendTo(block)
			.on("click", () => this.open_signature_pad(label, fieldname));
	}

	open_signature_pad(label, fieldname) {
		const dialog = new frappe.ui.Dialog({
			title: label,
			fields: [{ fieldname: "signature", fieldtype: "Signature", label: label }],
			primary_action_label: __("Save"),
			primary_action: (values) => {
				dialog.hide();
				this.doc.header[fieldname] = values.signature;
				this.queue_field(fieldname, values.signature);
				this.flush().then(() => this.render());
			},
		});
		dialog.show();
	}

	render_nav() {
		const nav = $('<div class="qw-nav"></div>').appendTo(this.page.main);
		this.page.main.find(".qw-nav").not(nav).remove();
		const last = this.section_index === this.sections().length - 1;

		$(`<button>${__("Back")}</button>`)
			.appendTo(nav)
			.prop("disabled", this.section_index === 0)
			.on("click", () => {
				this.section_index = Math.max(0, this.section_index - 1);
				this.render();
			});

		if (last) {
			$(`<button class="qw-primary">${__("Finish and submit")}</button>`)
				.appendTo(nav)
				.on("click", () => this.finish());
		} else {
			$(`<button class="qw-primary">${__("Next")}</button>`)
				.appendTo(nav)
				.on("click", () => {
					this.section_index = Math.min(this.sections().length - 1, this.section_index + 1);
					this.render();
				});
		}
	}

	render_save_state() {
		this.page.main.find(".qw-savestate").remove();
		this.save_state_el = $('<div class="qw-savestate"></div>').appendTo(this.page.main);
	}

	render_submitted() {
		const state = this.doc.state;
		const tone = state.fail_count ? "qw-banner-red" : "qw-banner-green";
		const line = state.fail_count
			? __("Submitted with {0} failing checks. A non-conformance and a corrective action have been raised for each.", [
					state.fail_count,
			  ])
			: __("Submitted. Every check passed.");
		this.body.html(`<div class="qw-banner ${tone}">${line}</div>
			<div class="qw-empty">
				<p><a href="/app/project-quality-inspection/${encodeURIComponent(this.doc.header.name)}">${__("Open the record")}</a></p>
				<p><a href="/app/inspection-wizard">${__("Back to my inspections")}</a></p>
			</div>`);
	}

	// ------------------------------------------------------------------- saving

	queue_field(field, value) {
		this.pending.fields[field] = value;
		this.schedule_save();
	}

	queue_row(name, changes) {
		this.pending.rows[name] = Object.assign({}, this.pending.rows[name] || {}, changes);
		this.schedule_save();
	}

	schedule_save() {
		this.set_save_state("pending");
		clearTimeout(this.save_timer);
		this.save_timer = setTimeout(() => this.flush(), 900);
	}

	flush() {
		if (this.saving || !this.has_pending()) return Promise.resolve();
		const patch = {
			fields: this.pending.fields,
			rows: Object.keys(this.pending.rows).map((name) =>
				Object.assign({ name: name }, this.pending.rows[name])
			),
		};
		this.pending = { fields: {}, rows: {} };
		this.saving = true;
		this.set_save_state("saving");

		return frappe
			.call({
				method: "erpnext_enhancements.api.quality_wizard.save_inspection",
				args: {
					inspection: this.doc.header.name,
					patch: JSON.stringify(patch),
					modified: this.doc.state.modified,
				},
			})
			.then((r) => {
				this.saving = false;
				if (!r || !r.message) return;
				this.apply_state(r.message);
				this.set_save_state("saved");
				this.render();
			})
			.catch(() => {
				this.saving = false;
				// Put the patch back so nothing is lost, then say so and retry. A silent failed
				// autosave loses a whole inspection on a bad signal, and the inspector finds out
				// after they have left the site.
				this.pending = {
					fields: Object.assign({}, patch.fields, this.pending.fields),
					rows: patch.rows.reduce((acc, row) => {
						acc[row.name] = Object.assign({}, row, this.pending.rows[row.name] || {});
						delete acc[row.name].name;
						return acc;
					}, this.pending.rows),
				};
				this.set_save_state("error");
				this.schedule_retry();
			});
	}

	schedule_retry() {
		clearTimeout(this.retry_timer);
		this.retry_timer = setTimeout(() => this.flush(), 8000);
	}

	apply_state(state) {
		this.doc.state = state;
		const by_name = {};
		(state.rows || []).forEach((row) => {
			by_name[row.name] = row;
		});
		this.sections().forEach((section) => {
			section.rows.forEach((row) => {
				const fresh = by_name[row.name];
				if (!fresh) return;
				// out_of_range is the server's answer, not ours. Two implementations of the
				// same arithmetic is one more than can stay correct.
				row.out_of_range = fresh.out_of_range;
				row.outcome = fresh.outcome;
			});
		});
	}

	set_save_state(kind) {
		if (!this.save_state_el) return;
		const labels = {
			pending: __("Not saved yet"),
			saving: __("Saving..."),
			saved: __("Saved"),
			error: __("Not saved - retrying"),
		};
		this.save_state_el
			.toggleClass("qw-err", kind === "error")
			.html(`<span>${labels[kind] || ""}</span>`);
		if (kind === "saved") {
			setTimeout(() => {
				if (this.save_state_el) this.save_state_el.html("");
			}, 1600);
		}
	}

	// ------------------------------------------------------------------ finish

	finish() {
		this.flush().then(() => {
			if (this.has_pending()) {
				frappe.msgprint({
					title: __("Still saving"),
					message: __("Some answers have not reached the server yet. Wait for Saved, then finish."),
					indicator: "orange",
				});
				return;
			}
			frappe.confirm(
				__("Submit this inspection? Failed checks will raise a non-conformance and a corrective action, and the record can no longer be edited."),
				() => {
					frappe.call({
						method: "erpnext_enhancements.api.quality_wizard.finish_inspection",
						args: { inspection: this.doc.header.name, modified: this.doc.state.modified },
						freeze: true,
						freeze_message: __("Submitting..."),
						callback: (r) => {
							if (!r || !r.message) return;
							this.doc.state = r.message;
							this.render();
						},
					});
				}
			);
		});
	}
}

// ---------------------------------------------------------------------------
// Row chips.
//
// is_mandatory and requires_photo reach the submit gate whether or not anybody saw them. A
// check that is only revealed as required by a refusal — after the inspector has packed up and
// driven away — is a checklist that lied about what it wanted.
// ---------------------------------------------------------------------------

function qw_required_chip(row) {
	const chips = [];
	if (row.is_mandatory) chips.push(`<span class="qw-chip qw-chip-req">${__("Required")}</span>`);
	if (row.requires_photo) chips.push(`<span class="qw-chip qw-chip-photo">${__("Photo")}</span>`);
	if (row.source === "Carried Action") {
		chips.push(`<span class="qw-chip qw-chip-carried">${__("Re-check")}</span>`);
	}
	if (row.source === "Project Addendum") {
		chips.push(`<span class="qw-chip qw-chip-addendum">${__("Contracted")}</span>`);
	}
	return chips.join("");
}

function qw_range_text(row) {
	const low = row.min_value;
	const high = row.max_value;
	const uom = row.uom ? ` ${frappe.utils.escape_html(row.uom)}` : "";
	if (low != null && high != null) return `${low} - ${high}${uom}`;
	if (low != null) return `${__("at least")} ${low}${uom}`;
	if (high != null) return `${__("at most")} ${high}${uom}`;
	return __("not specified");
}
