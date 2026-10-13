// Contract Builder: fill a contract in one step at a time, read it as you go, then sign or send it.
//
// Nik, 2026-10-13: "an interface that is a walkthrough of the contract fields that need to be
// filled out so we can basically build it alone or even with the client themselves … with a
// fully generated contract at the end and the digital esignature capture we can do right there
// or send to a customer to have them sign at their pace."
//
// Screens, each its own history entry so Back and Forward walk them (never break the browser's
// own navigation):
//
//   /desk/contract-builder                  start: pick a contract type, or open a draft
//   /desk/contract-builder/<contract>/<step> one step of the walk (a section of the doctype)
//   /desk/contract-builder/<contract>/review the whole agreement, what will print blank, sign/send
//   /desk/contract-builder/<contract>/sign   handing the device to the client to sign
//
// The steps are the Project Contract's own sections for this contract type
// (project_enhancements/contract_builder.py), so a field added to the doctype shows up here
// with nothing to change. Values are saved onto the real draft whenever the step changes; the
// preview beside the form re-renders from the values on screen, unsaved ones included, through
// the same renderer as the form's Preview button and the printed PDF.
//
// "Sign it here" issues the contract and opens the public signing page in a new tab on this
// device (esign.api.start_in_person_signing): the client re-types their email, initials each
// marked clause, signs, and is told to hand the device back, while this page watches for the
// signature. A new tab rather than a frame, because the signing page can go on to Stripe to save
// a card, and Stripe will not run inside a frame. "Send it to them" is the existing emailed link.

frappe.pages["contract-builder"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Contract Builder"),
		single_column: true,
	});
	wrapper.contract_builder = new ContractBuilder(page, wrapper);
};

frappe.pages["contract-builder"].on_page_show = function (wrapper) {
	if (wrapper.contract_builder) {
		wrapper.contract_builder.handle_route();
	}
};

const CB_API = "erpnext_enhancements.project_enhancements.contract_builder.";
const CB_CONTRACT = "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract.";
const CB_ESIGN = "erpnext_enhancements.project_enhancements.esign.api.";
// Shared with public/js/project_enhancements/contract_viewer.js, so the agreement's print CSS is
// published once per page however many places show a contract.
const CB_STYLE_ID = "ee-contract-viewer-style";
const CB_PAPER_CSS =
	".contract-doc { background: #fff; color: #000; padding: 16px 20px; border-radius: 6px; }";
const CB_REVIEW = "review";
const CB_SIGN = "sign";
const CB_POLL_MS = 4000;

const CB_STYLE = `
.cb-wrap{max-width:1500px;margin:0 auto;padding:8px 0 96px;color:var(--text-color);}
.cb-grid{display:grid;grid-template-columns:220px minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start;}
.cb-grid.cb-no-preview{grid-template-columns:220px minmax(0,1fr);}
@media (max-width:1199px){.cb-grid,.cb-grid.cb-no-preview{grid-template-columns:minmax(0,1fr);}.cb-steps{display:flex;overflow-x:auto;gap:6px;}.cb-steps .cb-step{white-space:nowrap;}}
.cb-steps{position:sticky;top:70px;}
.cb-step{display:flex;gap:8px;align-items:center;width:100%;text-align:left;background:none;border:0;border-radius:8px;padding:8px 10px;color:var(--text-muted);cursor:pointer;font-size:14px;}
.cb-step:hover{background:var(--control-bg);}
.cb-step.is-current{background:var(--control-bg);color:var(--text-color);font-weight:600;}
.cb-num{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;border:1px solid var(--border-color);font-size:12px;flex:none;}
.cb-step.is-current .cb-num{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;}
.cb-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin:6px 0 2px;}
.cb-head h3{margin:0;font-size:18px;font-weight:600;}
.cb-muted{color:var(--text-muted);font-size:13px;}
.cb-chip{display:inline-block;font-size:12px;border-radius:10px;padding:1px 9px;background:var(--control-bg);color:var(--text-color);margin-left:6px;}
.cb-chip.cb-green{background:#e7f7ed;color:#15803d;}.cb-chip.cb-blue{background:#e0efff;color:#1d4ed8;}.cb-chip.cb-orange{background:#fef3e2;color:#b45309;}
.cb-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:12px;padding:16px 18px;}
.cb-intro{font-size:14px;color:var(--text-muted);margin:4px 0 14px;}
.cb-nav{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;position:sticky;bottom:0;background:var(--bg-color);border-top:1px solid var(--border-color);padding:10px 0;margin-top:16px;z-index:2;}
.cb-nav .btn{margin-right:6px;}
.cb-preview{position:sticky;top:70px;max-height:calc(100vh - 90px);display:flex;flex-direction:column;}
.cb-preview-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;}
.cb-preview-body{overflow-y:auto;border:1px solid var(--border-color);border-radius:10px;padding:8px;background:var(--control-bg);flex:1;}
.cb-preview-body .contract-doc{font-size:12px;}
.cb-preview.is-loading .cb-preview-body{opacity:.6;}
@media (max-width:1199px){.cb-preview{position:static;max-height:none;display:none;}.cb-preview.is-open{display:flex;}.cb-preview-body{max-height:70vh;}}
.cb-toggle-preview{display:none;}
@media (max-width:1199px){.cb-toggle-preview{display:inline-block;}}
.cb-options{display:flex;flex-direction:column;gap:8px;}
.cb-option{display:grid;grid-template-columns:28px minmax(0,1fr) 140px 90px;gap:10px;align-items:center;border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;background:var(--card-bg);}
.cb-option.is-on{border-color:var(--primary,#2490ef);box-shadow:0 0 0 1px var(--primary,#2490ef) inset;}
.cb-option input[type=checkbox]{width:18px;height:18px;}
.cb-option .cb-option-label{font-weight:600;font-size:14px;}
.cb-option input.form-control{height:32px;}
@media (max-width:600px){.cb-option{grid-template-columns:28px minmax(0,1fr);}.cb-option .cb-option-money,.cb-option .cb-option-unit{grid-column:2;}}
.cb-blanks{border-left:4px solid #d97706;background:#fef3e2;color:#7c2d12;padding:10px 14px;border-radius:0 8px 8px 0;margin:0 0 14px;font-size:14px;}
.cb-blanks ul{margin:6px 0 0;padding-left:18px;}
.cb-blanks a{cursor:pointer;text-decoration:underline;}
.cb-ok{border-left:4px solid #15803d;background:#e7f7ed;color:#14532d;padding:10px 14px;border-radius:0 8px 8px 0;margin:0 0 14px;font-size:14px;}
.cb-actions{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0;}
.cb-review-doc{border:1px solid var(--border-color);border-radius:10px;padding:8px;background:var(--control-bg);max-height:70vh;overflow-y:auto;}
.cb-types{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin:12px 0 24px;}
.cb-type{text-align:left;cursor:pointer;color:var(--text-color);}
.cb-type:hover,.cb-type:focus{border-color:var(--primary,#2490ef);outline:none;}
.cb-type h4{margin:0 0 4px;font-size:15px;font-weight:600;}
.cb-list{display:flex;flex-direction:column;gap:6px;margin:8px 0 20px;}
.cb-row{display:flex;justify-content:space-between;gap:8px;align-items:center;cursor:pointer;padding:10px 14px;}
.cb-row:hover{border-color:var(--primary,#2490ef);}
.cb-sign-hero{text-align:center;padding:32px 18px;}
.cb-sign-hero h2{font-size:22px;margin:0 0 8px;}
.cb-sign-hero p{font-size:15px;max-width:620px;margin:6px auto;}
.cb-pulse{display:inline-block;width:10px;height:10px;border-radius:50%;background:#2490ef;margin-right:6px;animation:cb-pulse 1.4s ease-in-out infinite;}
@keyframes cb-pulse{50%{opacity:.25;}}
`;

class ContractBuilder {
	constructor(page, wrapper) {
		this.page = page;
		this.$root = $('<div class="cb-wrap"></div>').appendTo(page.body);
		if (!document.getElementById("cb-style")) {
			$('<style id="cb-style"></style>').text(CB_STYLE).appendTo(document.head);
		}
		this.data = null; // get_builder payload
		this.values = null; // the contract's values as edited on screen
		this.dirty = false;
		this.fg = null;
		this.step_index = 0;
		this.loading = false;
		this.preview_timer = null;
		this.preview_seq = 0;
		this.sign_url = null; // held in memory only: it is a bearer link, never put in the URL
		this.poll = null;

		// Leaving the builder by any other way (the sidebar, the awesome bar, a link) saves what
		// was typed on the step, and closing or reloading the tab with unsaved values asks first.
		$(wrapper).on("hide", () => {
			this.stop_poll();
			this.flush().catch(() => {});
		});
		window.addEventListener("beforeunload", (event) => {
			if ((frappe.get_route() || [])[0] !== "contract-builder") return;
			if (!this.data || !this.data.editable || !(this.dirty || this.tables_changed())) return;
			event.preventDefault();
			event.returnValue = "";
		});
	}

	// ------------------------------------------------------------------ routing

	handle_route() {
		const route = frappe.get_route();
		const name = route[1] ? decodeURIComponent(route[1]) : null;
		const step = route[2] ? decodeURIComponent(route[2]) : null;
		this.stop_poll();

		if (!name) {
			this.page.set_title(__("Contract Builder"));
			return this.flush()
				.catch(() => {})
				.then(() => {
					this.data = null;
					this.show_start();
				});
		}

		if (this.data && this.data.name === name) {
			// Back or Forward between steps. Save what was typed on the way out; if the save is
			// refused (frappe has already said why), show the step anyway: the values stay on
			// screen, still unsaved, rather than being lost to a blank error page.
			return this.flush()
				.catch(() => {})
				.then(() => this.show(step));
		}
		return this.load(name)
			.then(() => this.show(step))
			.catch((error) => this.show_problem(error));
	}

	go(step, replace) {
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route("contract-builder", this.data.name, step);
		// Read by the router's push, which has already run; left set it would turn the next
		// step into a replace (the rental planner's pattern).
		delete frappe.route_flags.replace_route;
	}

	// Save, then move. A refused save keeps the user where they are: frappe has shown the reason.
	save_and_go(step) {
		this.flush()
			.then(() => this.go(step))
			.catch(() => {});
	}

	load(name) {
		this.$root.html(`<div class="cb-muted" style="padding:24px">${__("Loading…")}</div>`);
		return frappe.xcall(`${CB_API}get_builder`, { name }).then((data) => this.set_data(data));
	}

	set_data(data) {
		this.data = data;
		this.values = JSON.parse(JSON.stringify(data.doc || {}));
		this.dirty = false;
		this.page.set_title(data.title || data.name);
	}

	show(step) {
		const steps = this.data.steps || [];
		if (!step) {
			// The first visit to a contract lands on a step. A replace, not a new entry, so Back
			// from it leaves the builder rather than bouncing on a bare URL; through the router,
			// which renders the step when it lands (on_page_show).
			return this.go(this.data.docstatus === 0 && steps.length ? steps[0].key : CB_REVIEW, true);
		}
		if (step === CB_SIGN) return this.render_sign();
		if (step === CB_REVIEW) return this.render_review();
		const index = steps.findIndex((s) => s.key === step);
		if (index < 0) return this.go(CB_REVIEW, true);
		return this.render_step(index);
	}

	show_problem(error) {
		const message = (error && (error.message || error._server_messages)) || __("Something went wrong.");
		this.$root.html(
			`<div class="cb-card" style="margin-top:16px">${frappe.utils.escape_html(
				typeof message === "string" ? message : __("This contract could not be opened.")
			)}<div style="margin-top:10px"><a href="/desk/contract-builder">${__(
				"Back to the Contract Builder"
			)}</a></div></div>`
		);
	}

	// -------------------------------------------------------------- start screen

	show_start() {
		this.$root.html(`<div class="cb-muted" style="padding:24px">${__("Loading…")}</div>`);
		frappe.xcall(`${CB_API}get_builder_start`).then((start) => {
			const options = frappe.route_options || {};
			frappe.route_options = null; // consumed, so a later plain visit does not replay it
			const $wrap = $("<div></div>");
			$wrap.append(`
				<div class="cb-head"><h3>${__("Build a contract")}</h3></div>
				<p class="cb-intro">${__(
					"Pick the kind of agreement. The builder walks you through each part, shows the finished agreement as you go, and at the end the client can sign right here or get it by email to sign in their own time."
				)}</p>`);
			const $types = $('<div class="cb-types"></div>').appendTo($wrap);
			(start.types || []).forEach((type) => {
				$(`<button type="button" class="cb-card cb-type"></button>`)
					.append($("<h4></h4>").text(type.title))
					.append($('<div class="cb-muted"></div>').text(type.description || ""))
					.on("click", () => this.start_dialog(type, {}))
					.appendTo($types);
			});
			this.append_list($wrap, __("Drafts in progress"), start.drafts);
			this.append_list($wrap, __("Waiting for a signature"), start.awaiting);
			this.$root.empty().append($wrap);

			const wanted = (start.types || []).find((t) => t.name === options.template);
			if (wanted) this.start_dialog(wanted, options);
		});
	}

	append_list($wrap, title, rows) {
		if (!rows || !rows.length) return;
		$wrap.append(`<div class="cb-head"><h3 style="font-size:15px">${frappe.utils.escape_html(title)}</h3></div>`);
		const $list = $('<div class="cb-list"></div>').appendTo($wrap);
		rows.forEach((row) => {
			$(`<div class="cb-card cb-row" role="button" tabindex="0"></div>`)
				.append($("<div></div>").text(row.title || row.name))
				.append(
					$('<div class="cb-muted"></div>').text(
						[row.name, row.project, frappe.datetime.prettyDate(row.modified)].filter(Boolean).join(" · ")
					)
				)
				.on("click keydown", (event) => {
					if (event.type === "keydown" && event.key !== "Enter") return;
					frappe.set_route("contract-builder", row.name);
				})
				.appendTo($list);
		});
	}

	start_dialog(type, options) {
		const any_party = type.party_type === "Any Party";
		const supplier = type.party_type === "Supplier";
		const fields = [
			{
				fieldname: "project",
				fieldtype: "Link",
				options: "Project",
				label: __("Project"),
				default: options.project,
				description: __("The client, site and description come from the project."),
			},
		];
		if (!supplier && !any_party) {
			fields.push({
				fieldname: "opportunity",
				fieldtype: "Link",
				options: "Opportunity",
				label: __("Or an Opportunity"),
				default: options.opportunity,
				depends_on: "eval:!doc.project",
			});
		}
		if (any_party) {
			fields.push({
				fieldname: "party_type",
				fieldtype: "Select",
				label: __("Party Type"),
				options: "Customer\nSupplier\nEmployee",
				default: "Customer",
				reqd: 1,
			});
			fields.push({
				fieldname: "party",
				fieldtype: "Dynamic Link",
				options: "party_type",
				label: __("Party"),
				reqd: 1,
				get_options: () => dialog.get_value("party_type"),
			});
		} else {
			fields.push({
				fieldname: "party",
				fieldtype: "Link",
				options: type.party_type,
				label: supplier ? __("Subcontractor") : __("Client"),
				default: options.customer || options.supplier,
				reqd: supplier ? 1 : 0,
				description: supplier ? "" : __("Leave blank to take the project's customer."),
			});
		}
		const dialog = new frappe.ui.Dialog({
			title: __("New {0}", [type.title]),
			fields,
			primary_action_label: __("Start"),
			primary_action: (values) => {
				let source_doctype = null;
				let source_name = null;
				if (values.project) {
					source_doctype = "Project";
					source_name = values.project;
				} else if (values.opportunity) {
					source_doctype = "Opportunity";
					source_name = values.opportunity;
				} else if (supplier && values.party) {
					source_doctype = "Supplier";
					source_name = values.party;
				}
				dialog.disable_primary_action();
				frappe
					.xcall(`${CB_CONTRACT}create_contract`, {
						template: type.name,
						source_doctype,
						source_name,
						party: values.party || null,
					})
					.then((name) => {
						dialog.hide();
						frappe.set_route("contract-builder", name);
					})
					.catch(() => dialog.enable_primary_action());
			},
		});
		dialog.show();
	}

	// --------------------------------------------------------------------- layout

	frame(current, with_preview) {
		const data = this.data;
		const $grid = $(`<div class="cb-grid${with_preview ? "" : " cb-no-preview"}"></div>`);
		const $steps = $('<nav class="cb-steps" aria-label="Steps"></nav>').appendTo($grid);
		(data.steps || []).forEach((step, index) => {
			this.step_button(step.key, index + 1, step.title, current === step.key).appendTo($steps);
		});
		this.step_button(
			CB_REVIEW,
			(data.steps || []).length + 1,
			__("Review and sign"),
			current === CB_REVIEW || current === CB_SIGN
		).appendTo($steps);
		$('<div style="margin-top:12px"></div>')
			.append(
				$(`<a class="cb-muted" href="#"></a>`)
					.text(__("Open the full form"))
					.on("click", (event) => {
						event.preventDefault();
						this.flush()
							.then(() => frappe.set_route("Form", "Project Contract", data.name))
							.catch(() => {});
					})
			)
			.appendTo($steps);
		const $main = $('<div class="cb-main"></div>').appendTo($grid);
		const $head = $('<div class="cb-head"></div>').appendTo($main);
		$head.append($("<h3></h3>").text(data.template_title || ""));
		$head.append(
			$('<div class="cb-muted"></div>')
				.text(`${data.name} · ${this.values.party_display || this.values.party || __("no client yet")}`)
				.append(this.status_chip())
		);
		let $preview = null;
		if (with_preview) {
			$preview = $(`<aside class="cb-preview" aria-label="${__("Preview")}">
				<div class="cb-preview-head"><b>${__("The agreement so far")}</b><span class="cb-muted">${__(
					"Blank lines are still to fill"
				)}</span></div>
				<div class="cb-preview-body"></div></aside>`).appendTo($grid);
		}
		this.$root.empty().append($grid);
		this.$preview = $preview;
		return $main;
	}

	step_button(key, number, title, current) {
		return $(`<button type="button" class="cb-step${current ? " is-current" : ""}"></button>`)
			.attr("aria-current", current ? "step" : null)
			.append(`<span class="cb-num">${number}</span>`)
			.append($("<span></span>").text(title))
			.on("click", () => {
				if (!current) this.save_and_go(key);
			});
	}

	status_chip() {
		const status = this.values.status || (this.data.docstatus === 0 ? "Draft" : "");
		const colour = { Signed: "cb-green", "Out for Signature": "cb-blue", Void: "cb-orange" }[status] || "";
		return $(`<span class="cb-chip ${colour}"></span>`).text(__(status));
	}

	// ---------------------------------------------------------------------- a step

	render_step(index) {
		const step = this.data.steps[index];
		this.step_index = index;
		const editable = !!this.data.editable;
		const $main = this.frame(step.key, true);
		const $card = $('<div class="cb-card"></div>').appendTo($main);
		$card.append($("<h4 style='margin:0 0 2px;font-size:16px;font-weight:600'></h4>").text(step.title));
		if (step.intro) $card.append($('<p class="cb-intro"></p>').text(step.intro));
		if (!editable) {
			$card.append(
				`<p class="cb-intro">${__(
					"This contract has been issued, so its terms are fixed. To change them, cancel and amend it from the full form."
				)}</p>`
			);
		}
		const $body = $("<div></div>").appendTo($card);

		this.loading = true;
		const fields = step.fields.map((field) => this.control_def(field, editable));
		this.fg = new frappe.ui.FieldGroup({ fields, body: $body[0] });
		this.fg.make();
		const initial = {};
		step.fields.forEach((field) => {
			if (field.fieldtype === "Table" && !field.fixed_rows) return;
			if (this.values[field.fieldname] !== undefined) initial[field.fieldname] = this.values[field.fieldname];
		});
		this.fg.set_values(initial).then(() => {
			this.fg.refresh_dependency();
			this.loading = false;
		});
		step.fields
			.filter((field) => field.fieldtype === "Table" && field.fixed_rows)
			.forEach((field) => this.render_choices(field, editable));

		const $nav = $('<div class="cb-nav"></div>').appendTo($main);
		const $left = $("<div></div>").appendTo($nav);
		const $right = $("<div></div>").appendTo($nav);
		if (index > 0) {
			$(`<button class="btn btn-default btn-sm">${__("Back")}</button>`)
				.on("click", () => this.save_and_go(this.data.steps[index - 1].key))
				.appendTo($left);
		}
		$(`<button class="btn btn-default btn-sm cb-toggle-preview">${__("Preview")}</button>`)
			.on("click", () => this.$preview && this.$preview.toggleClass("is-open"))
			.appendTo($left);
		if (editable) {
			$(`<button class="btn btn-default btn-sm">${__("Save")}</button>`)
				.on("click", () => this.flush(true).catch(() => {}))
				.appendTo($right);
		}
		const next = this.data.steps[index + 1];
		$(`<button class="btn btn-primary btn-sm">${next ? __("Next: {0}", [next.title]) : __("Review and sign")}</button>`)
			.on("click", () => this.save_and_go(next ? next.key : CB_REVIEW))
			.appendTo($right);

		this.refresh_preview(true);
	}

	control_def(field, editable) {
		const df = Object.assign({}, field);
		delete df.child_fields;
		if (!editable) df.read_only = 1;
		["depends_on", "mandatory_depends_on", "read_only_depends_on"].forEach((key) => {
			if (df[key]) df[key] = this.condition(df[key]);
		});
		if (df.fieldtype === "Table" && field.fixed_rows) {
			// Drawn by render_choices into this placeholder: a menu of options, not a grid.
			return { fieldtype: "HTML", fieldname: `${field.fieldname}__choices`, label: field.label };
		}
		if (df.fieldtype === "Table") {
			const rows = (this.values[field.fieldname] || []).map((row) => Object.assign({}, row));
			// A cell edit fires the child field's onchange, never the table's, so each column
			// marks the step dirty itself; added and removed rows are caught by tables_changed().
			const touched = () => {
				if (this.loading) return;
				this.dirty = true;
				this.collect();
				this.refresh_preview();
			};
			df.fields = (field.child_fields || []).map((child) =>
				Object.assign({}, child, editable ? { onchange: touched } : { read_only: 1 })
			);
			df.data = rows;
			df.get_data = () => rows;
			df.in_place_edit = true;
			df.cannot_add_rows = !editable;
			df.cannot_delete_rows = !editable;
		}
		if (df.fieldtype === "Dynamic Link") {
			df.get_options = () => this.current("party_type");
		}
		df.onchange = () => {
			if (this.loading) return;
			this.dirty = true;
			this.collect();
			if (this.fg) this.fg.refresh_dependency();
			this.refresh_preview();
		};
		return df;
	}

	// depends_on is written against the whole contract (doc.template_key, doc.payment_method …),
	// but a FieldGroup only knows the fields of this one step. Evaluate it against the contract's
	// values with the step's live inputs laid over them.
	condition(expression) {
		if (typeof expression !== "string") return expression;
		return (step_values) => {
			const doc = Object.assign({}, this.values, step_values || {});
			if (expression.startsWith("eval:")) {
				return frappe.utils.eval(expression.slice(5), { doc, parent: doc });
			}
			const value = doc[expression];
			return Array.isArray(value) ? value.length > 0 : !!value;
		};
	}

	current(fieldname) {
		if (this.fg && this.fg.fields_dict[fieldname]) return this.fg.get_value(fieldname);
		return this.values[fieldname];
	}

	// Every input of the step into this.values, empties included: FieldGroup.get_values() drops
	// an empty field, which would make clearing one impossible.
	collect() {
		if (!this.fg) return;
		Object.keys(this.fg.fields_dict).forEach((fieldname) => {
			const control = this.fg.fields_dict[fieldname];
			if (!control.get_value || fieldname.endsWith("__choices")) return;
			if (["HTML", "Column Break", "Section Break", "Button"].includes(control.df.fieldtype)) return;
			const value = control.get_value();
			this.values[fieldname] = value === undefined ? null : value;
		});
	}

	// A fixed menu of rows (the maintenance plan's options, an owner contract's phases): tick it,
	// price it. The rows are the contract's own; nothing is added or removed here.
	render_choices(field, editable) {
		const control = this.fg.fields_dict[`${field.fieldname}__choices`];
		if (!control) return;
		const rows = this.values[field.fieldname] || [];
		const children = field.child_fields || [];
		const tick = children.find((c) => c.fieldtype === "Check");
		const label = children.find((c) => c.fieldtype === "Data" && c.read_only);
		const money = children.filter((c) => ["Currency", "Float", "Int"].includes(c.fieldtype) && !c.hidden);
		const text = children.filter((c) => c.fieldtype === "Data" && !c.read_only && !c.hidden);
		const $list = $('<div class="cb-options"></div>');
		if (field.label) $list.append($("<label class='control-label'></label>").text(__(field.label)));
		rows.forEach((row) => {
			const $row = $('<div class="cb-option"></div>').toggleClass("is-on", !!(tick && row[tick.fieldname]));
			const $check = $('<input type="checkbox">')
				.prop("checked", !!(tick && row[tick.fieldname]))
				.prop("disabled", !editable)
				.attr("aria-label", row[label ? label.fieldname : "name"] || "")
				.on("change", () => {
					row[tick.fieldname] = $check.prop("checked") ? 1 : 0;
					$row.toggleClass("is-on", !!row[tick.fieldname]);
					this.choice_changed();
				});
			$row.append($("<div></div>").append($check));
			$row.append($('<div class="cb-option-label"></div>').text(label ? row[label.fieldname] || "" : ""));
			money.forEach((child) => {
				const $input = $('<input type="number" step="0.01" min="0" class="form-control">')
					.attr("placeholder", __(child.label))
					.attr("aria-label", __(child.label))
					.val(row[child.fieldname] || "")
					.prop("disabled", !editable)
					.on("input", () => {
						row[child.fieldname] = $input.val() === "" ? null : flt($input.val());
						this.choice_changed();
					});
				$row.append($('<div class="cb-option-money"></div>').append($input));
			});
			text.forEach((child) => {
				const $unit = $('<div class="cb-option-unit cb-muted"></div>').text(
					row[child.fieldname] ? __("per {0}", [row[child.fieldname]]) : ""
				);
				$row.append($unit);
			});
			$list.append($row);
		});
		$(control.wrapper).empty().append($list);
	}

	choice_changed() {
		this.dirty = true;
		this.refresh_preview();
	}

	// -------------------------------------------------------------------- saving

	// Rows added to or removed from a grid fire no change at all, so compare the step's tables
	// with what was last saved.
	tables_changed() {
		if (!this.fg || !this.data) return false;
		const step = this.data.steps[this.step_index];
		if (!step) return false;
		return step.fields.some((field) => {
			if (field.fieldtype !== "Table" || field.fixed_rows) return false;
			const control = this.fg.fields_dict[field.fieldname];
			if (!control || !control.get_value) return false;
			const names = (field.child_fields || []).map((child) => child.fieldname);
			const shape = (rows) =>
				JSON.stringify(
					(rows || []).map((row) => names.map((name) => (row[name] === undefined ? null : row[name])))
				);
			return shape(control.get_value()) !== shape(this.data.doc[field.fieldname]);
		});
	}

	flush(announce) {
		if (this.data && this.data.editable && !this.dirty && this.tables_changed()) this.dirty = true;
		if (!this.data || !this.dirty || !this.data.editable) {
			if (announce) frappe.show_alert({ message: __("Saved"), indicator: "green" });
			return Promise.resolve();
		}
		this.collect();
		const step_key = this.data.steps[this.step_index] && this.data.steps[this.step_index].key;
		return frappe
			.xcall(`${CB_API}save_builder`, { name: this.data.name, values: JSON.stringify(this.values) })
			.then((data) => {
				this.set_data(data);
				if (announce) {
					frappe.show_alert({ message: __("Saved"), indicator: "green" });
					// Totals and the party's name are worked out on save: show them.
					if (step_key) this.show(step_key);
				}
			});
	}

	// ------------------------------------------------------------------- preview

	refresh_preview(now) {
		if (!this.$preview) return;
		clearTimeout(this.preview_timer);
		this.preview_timer = setTimeout(() => this.paint_preview(this.$preview.find(".cb-preview-body")), now ? 0 : 600);
	}

	paint_preview($target, args) {
		const seq = ++this.preview_seq;
		const $holder = $target.closest(".cb-preview");
		$holder.addClass("is-loading");
		return frappe
			.xcall(`${CB_CONTRACT}get_contract_html`, args || { doc: JSON.stringify(this.values) })
			.then((payload) => {
				if (seq !== this.preview_seq || !payload) return;
				this.ensure_styles(payload.css);
				const $doc = $('<div class="contract-doc"></div>')
					.addClass(this.data.template_key ? `contract-${this.data.template_key}` : "")
					.html(payload.html);
				$target.empty();
				if (payload.executed) {
					$target.append(
						$('<div class="cb-muted" style="margin-bottom:8px"></div>').text(
							__("Executed copy: the document that was signed.")
						)
					);
				}
				$target.append($doc);
			})
			.finally(() => $holder.removeClass("is-loading"));
	}

	ensure_styles(css) {
		let style = document.getElementById(CB_STYLE_ID);
		if (!style) {
			style = document.createElement("style");
			style.id = CB_STYLE_ID;
			document.head.appendChild(style);
		}
		const text = `${CB_PAPER_CSS}\n${css || ""}`;
		if (style.textContent !== text) style.textContent = text;
	}

	// -------------------------------------------------------------------- review

	render_review() {
		this.fg = null;
		const data = this.data;
		const $main = this.frame(CB_REVIEW, false);
		const signed = data.status === "Signed";
		const $card = $('<div class="cb-card"></div>').appendTo($main);
		$card.append(
			`<h4 style="margin:0 0 2px;font-size:16px;font-weight:600">${
				signed ? __("Signed") : __("Review and sign")
			}</h4>`
		);

		if (!signed && data.docstatus === 0) {
			if (data.blanks && data.blanks.length) {
				const $blanks = $(`<div class="cb-blanks"><b>${__(
					"These will print as blank lines:"
				)}</b><ul></ul><div style="margin-top:6px">${__(
					"That is fine for anything the client fills in by hand; otherwise go back and fill them in."
				)}</div></div>`);
				data.blanks.forEach((blank) => {
					const step = data.steps[blank.step];
					$("<li></li>")
						.append(document.createTextNode(`${blank.label} `))
						.append(
							step
								? $("<a></a>")
										.text(__("({0})", [step.title]))
										.on("click", () => this.go(step.key))
								: ""
						)
						.appendTo($blanks.find("ul"));
				});
				$card.append($blanks);
			} else {
				$card.append(`<div class="cb-ok">${__("Every field the agreement prints is filled in.")}</div>`);
			}
		}

		const $actions = $('<div class="cb-actions"></div>').appendTo($card);
		this.review_actions($actions);

		const $doc = $('<div class="cb-review-doc"></div>').appendTo($card);
		this.paint_preview($doc, data.docstatus === 0 ? null : { name: data.name });
	}

	review_actions($actions) {
		const data = this.data;
		const button = (label, cls, handler) =>
			$(`<button class="btn ${cls}"></button>`).text(label).on("click", handler).appendTo($actions);

		if (data.status === "Signed") {
			button(__("Open the contract"), "btn-default", () =>
				frappe.set_route("Form", "Project Contract", data.name)
			);
			if (data.maintenance_contract) {
				button(__("Open the maintenance contract"), "btn-primary", () =>
					frappe.set_route("Form", "Sapphire Maintenance Contract", data.maintenance_contract)
				);
			}
			return;
		}
		if (data.docstatus === 2) {
			$actions.append(
				`<div class="cb-muted">${__("This contract was cancelled. Amend it from the full form to make a new revision.")}</div>`
			);
			return;
		}
		const live = ["Sent", "Viewed"].includes((data.signature || {}).status) ? data.signature : null;
		if (live) {
			$actions.append(
				$('<div class="cb-muted" style="flex-basis:100%"></div>').text(
					live.signing_channel === "In Person"
						? __("Open for signing on a device here.")
						: __("Out for signature to {0}; {1}.", [
								live.signer_email,
								live.view_count ? __("they have opened it") : __("not opened yet"),
						  ])
				)
			);
		}
		if (data.can_sign) {
			button(__("Sign it here now"), "btn-primary", () => this.sign_here_dialog());
			button(__("Send it to them to sign"), "btn-default", () => this.send_dialog());
		} else if (data.can_issue) {
			button(__("Issue the contract"), "btn-primary", () => this.issue_only());
		}
		if (data.docstatus === 0 && data.editable) {
			button(__("Save draft"), "btn-default", () => this.flush(true).catch(() => {}));
		}
		if (data.docstatus === 1) {
			button(__("Print / PDF"), "btn-default", () => frappe.set_route("print", "Project Contract", data.name));
		}
		if (!data.esign_enabled) {
			$actions.append(
				`<div class="cb-muted" style="flex-basis:100%">${__(
					"Online signing is switched off, so print the issued contract for a wet signature and record it with Mark as Signed on the full form."
				)}</div>`
			);
		}
	}

	confirm_issue_text() {
		return this.data.docstatus === 0
			? __(
					"This issues {0}: its terms are locked from here, and a change after this means a new revision.",
					[this.data.name]
			  )
			: "";
	}

	issue() {
		if (this.data.docstatus !== 0) return Promise.resolve();
		return this.flush().then(() =>
			frappe.xcall(`${CB_API}issue_contract`, { name: this.data.name }).then((data) => this.set_data(data))
		);
	}

	issue_only() {
		frappe.confirm(this.confirm_issue_text(), () =>
			this.issue().then(() => {
				frappe.show_alert({ message: __("Issued"), indicator: "green" });
				this.render_review();
			})
		);
	}

	live_emailed_link() {
		const sig = this.data.signature || {};
		return ["Sent", "Viewed"].includes(sig.status) && sig.signing_channel !== "In Person" ? sig : null;
	}

	sign_here_dialog() {
		const emailed = this.live_emailed_link();
		const replaces = emailed
			? `<p class="text-warning">${frappe.utils.escape_html(
					__(
						"This replaces the signing link already emailed to {0}: that link stops working. If they would rather sign from the email, close this and leave it be.",
						[emailed.signer_email]
					)
			  )}</p>`
			: "";
		const dialog = new frappe.ui.Dialog({
			title: __("Sign it here now"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p>${__(
						"The agreement opens on a new tab, ready to hand to the client. They re-type their email, initial each marked clause, and sign. Their signed copy is emailed to them."
					)}</p><p class="text-muted">${__(
						"This device stays signed in as you, so stay with them while they sign."
					)}</p>${replaces}<p class="text-muted">${frappe.utils.escape_html(this.confirm_issue_text())}</p>`,
				},
				{
					fieldname: "email",
					fieldtype: "Data",
					options: "Email",
					label: __("Client's email"),
					default: this.values.contact_email,
					reqd: 1,
				},
			],
			primary_action_label: __("Open it for signing"),
			primary_action: (values) => {
				// Opened now, inside the click, so a popup blocker lets it through; it is pointed
				// at the signing page once the link exists.
				const tab = window.open("", "_blank");
				if (tab) {
					// The client holds this tab: it must not be able to reach back into the
					// staff member's desk window through window.opener.
					try {
						tab.opener = null;
					} catch (e) {
						// Some browsers refuse the assignment; the tab still works.
					}
					tab.document.write(
						`<p style="font-family:sans-serif;padding:24px">${__("Preparing the agreement…")}</p>`
					);
				}
				dialog.hide();
				this.issue()
					.then(() =>
						frappe.xcall(`${CB_ESIGN}start_in_person_signing`, {
							project_contract: this.data.name,
							signer_email: values.email,
						})
					)
					.then((result) => {
						this.sign_url = result.url;
						if (tab) tab.location.href = result.url;
						this.go(CB_SIGN);
					})
					.catch(() => {
						if (tab) tab.close();
					});
			},
		});
		dialog.show();
	}

	send_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Send it to them to sign"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p>${__(
						"They get an email with a link to read, initial and sign the agreement in their own time."
					)}</p><p class="text-muted">${frappe.utils.escape_html(this.confirm_issue_text())}</p>`,
				},
				{
					fieldname: "recipient_email",
					fieldtype: "Data",
					options: "Email",
					label: __("Send to"),
					default: this.values.contact_email,
					reqd: 1,
					description: __("They'll be asked to re-type this address to confirm it's them."),
				},
				{
					fieldname: "message",
					fieldtype: "Small Text",
					label: __("Note (optional)"),
					description: __("Included in the email above the signing button."),
				},
			],
			primary_action_label: __("Send"),
			primary_action: (values) => {
				dialog.hide();
				this.issue()
					.then(() =>
						frappe.xcall(`${CB_ESIGN}send_for_signature`, {
							project_contract: this.data.name,
							recipient_email: values.recipient_email,
							message: values.message,
						})
					)
					.then((result) => {
						frappe.show_alert({ message: __("Sent to {0}", [result.to]), indicator: "green" }, 6);
						return this.load(this.data.name).then(() => this.render_review());
					});
			},
		});
		dialog.show();
	}

	// -------------------------------------------------------------------- signing

	render_sign() {
		this.fg = null;
		const $main = this.frame(CB_SIGN, false);
		const party = this.values.party_display || this.values.party || __("the client");
		const $card = $('<div class="cb-card cb-sign-hero"></div>').appendTo($main);
		if (this.data.status === "Signed") {
			// Signed while this screen watched. It stays this entry's screen, so Back still goes
			// to the review it came from (which now shows the executed copy) without a repeat.
			$card.append($("<h2></h2>").text(__("Signed")));
			$card.append(
				$("<p></p>").text(
					__("{0} has signed {1}. Their copy is on its way to them by email.", [
						this.values.signed_by || party,
						this.data.name,
					])
				)
			);
			const $done = $('<div class="cb-actions" style="justify-content:center"></div>').appendTo($card);
			$(`<button class="btn btn-primary">${__("See the signed agreement")}</button>`)
				.on("click", () => this.go(CB_REVIEW))
				.appendTo($done);
			if (this.data.maintenance_contract) {
				$(`<button class="btn btn-default">${__("Open the maintenance contract")}</button>`)
					.on("click", () =>
						frappe.set_route("Form", "Sapphire Maintenance Contract", this.data.maintenance_contract)
					)
					.appendTo($done);
			}
			return;
		}
		const $status = $("<div></div>");
		$card.append($("<h2></h2>").text(__("Hand the device to {0}", [party])));
		$card.append(
			$("<p></p>").text(
				__(
					"The agreement is open on the other tab. When they have signed, the page tells them to hand the device back, and this screen updates by itself."
				)
			)
		);
		$card.append($status);
		const $actions = $('<div class="cb-actions" style="justify-content:center"></div>').appendTo($card);
		if (this.sign_url) {
			$(`<button class="btn btn-primary">${__("Open the signing page")}</button>`)
				.on("click", () => window.open(this.sign_url, "_blank"))
				.appendTo($actions);
		} else {
			$(`<button class="btn btn-primary">${__("Open it for signing on this device")}</button>`)
				.on("click", () => this.sign_here_dialog())
				.appendTo($actions);
		}
		$(`<button class="btn btn-default">${__("Email it to them instead")}</button>`)
			.on("click", () => this.send_dialog())
			.appendTo($actions);
		$(`<button class="btn btn-default">${__("Back to the review")}</button>`)
			.on("click", () => this.go(CB_REVIEW))
			.appendTo($actions);

		const check = () =>
			frappe.xcall(`${CB_API}get_signing_status`, { name: this.data.name }).then((state) => {
				const request = state.request || {};
				if (state.contract_status === "Signed" || request.status === "Signed") {
					this.stop_poll();
					this.sign_url = null;
					frappe.show_alert({ message: __("Signed by {0}", [request.signed_name || party]), indicator: "green" }, 8);
					return this.load(this.data.name).then(() => this.render_sign());
				}
				const seen = request.view_count ? __("The agreement is open on the other tab.") : __("Waiting for the agreement to open…");
				$status.html(`<p class="cb-muted"><span class="cb-pulse"></span>${frappe.utils.escape_html(seen)}</p>`);
			});
		check();
		this.poll = setInterval(() => {
			if (frappe.get_route()[0] !== "contract-builder") return this.stop_poll();
			check();
		}, CB_POLL_MS);
	}

	stop_poll() {
		if (this.poll) clearInterval(this.poll);
		this.poll = null;
	}
}
