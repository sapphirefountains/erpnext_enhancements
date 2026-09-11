// Visit Wizard — the technician's guided, touch-first maintenance visit form.
//
// A desk Page (/app/visit-wizard?record=MNT-REC-...) that steps through a
// Sapphire Maintenance Record one section at a time: Safety → Water
// Chemistry → Chemicals Used → Inspection → Cleaning → Wrap-up. Every input
// is a tap (steppers, segmented buttons, toggles) or a short numeric entry —
// no child-table grids. Without ?record= it lists today's open visits.
//
// It reads and writes the same Sapphire Maintenance Record as the desk form
// via api/maintenance_visit.py (bootstrap instantiates the template
// server-side; step changes autosave a field-allowlisted patch with
// optimistic locking; Finish applies the house workflow action). All
// downstream automation is untouched. The desk form remains the
// supervisor/review surface.
//
// Styling uses Frappe CSS variables so Frappe Light and Timeless Night both
// work; semantic pass/fail colors stay literal (house convention). The
// page-loader serves this file version-aware, so no .bundle.* is needed.

frappe.pages["visit-wizard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Visit Wizard"),
		single_column: true,
	});
	wrapper.visit_wizard = new VisitWizard(page, wrapper);
};

frappe.pages["visit-wizard"].on_page_show = function (wrapper) {
	if (wrapper.visit_wizard) {
		wrapper.visit_wizard.handle_route();
	}
};

const VZ_STYLE = `
.vz-wrap{max-width:640px;margin:0 auto;padding-bottom:96px;font-size:15px;}
.vz-muted{color:var(--text-muted);font-size:13px;}
.vz-progress{height:6px;border-radius:3px;background:var(--control-bg);margin:8px 0 4px;overflow:hidden;}
.vz-progress>div{height:100%;border-radius:3px;background:var(--primary,#2490ef);transition:width .25s;}
.vz-stepline{display:flex;justify-content:space-between;align-items:center;font-size:14px;color:var(--text-muted);margin-bottom:10px;}
.vz-tabs{display:flex;gap:8px;overflow-x:auto;padding:2px 0 10px;position:sticky;top:0;background:var(--bg-color);z-index:3;}
.vz-tab{flex:0 0 auto;padding:9px 15px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:15px;cursor:pointer;white-space:nowrap;}
.vz-tab.vz-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.vz-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);}
.vz-card.vz-bad{border-color:#dc2626;background:rgba(220,38,38,.06);}
.vz-card.vz-done{border-color:#15803d;}
.vz-card-title{font-weight:600;font-size:17px;}
.vz-card-sub{font-size:14px;color:var(--text-muted);margin-top:3px;}
.vz-chip{display:inline-block;font-size:13px;border-radius:10px;padding:2px 9px;margin-left:6px;vertical-align:middle;}
.vz-chip-red{background:#fde8e8;color:#b91c1c;}
.vz-chip-green{background:#e7f7ed;color:#15803d;}
.vz-chip-req{background:#fef3c7;color:#92400e;font-weight:600;}
.vz-step-dots{display:flex;gap:5px;margin:0 0 10px;}
.vz-step-dot{flex:1;height:4px;border-radius:2px;background:var(--control-bg);border:none;padding:0;cursor:pointer;}
.vz-step-dot.vz-done{background:#15803d;}
.vz-step-dot.vz-todo{background:#f59e0b;}
.vz-step-dot.vz-here{background:var(--primary,#2490ef);}
.vz-savestate{position:fixed;left:0;right:0;bottom:calc(74px + env(safe-area-inset-bottom));text-align:center;font-size:13px;pointer-events:none;z-index:6;}
.vz-savestate span{display:inline-block;padding:4px 12px;border-radius:12px;background:var(--control-bg);color:var(--text-muted);border:1px solid var(--border-color);}
.vz-savestate.vz-err span{background:#fde8e8;color:#b91c1c;border-color:#b91c1c;font-weight:600;pointer-events:auto;}
.vz-num{width:100%;margin-top:10px;font-size:26px;text-align:center;padding:11px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.vz-num:focus{outline:2px solid var(--primary,#2490ef);}
.vz-stepper{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;}
.vz-step-btn{flex:0 0 56px;height:50px;font-size:26px;line-height:1;border-radius:10px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);cursor:pointer;}
.vz-step-btn:active{background:var(--border-color);}
.vz-qty{flex:1;text-align:center;font-size:26px;font-weight:700;cursor:pointer;color:var(--text-color);}
.vz-qty input{width:96px;font-size:24px;text-align:center;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg);color:var(--text-color);padding:6px;}
.vz-seg{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;}
.vz-seg button{flex:1 1 calc(25% - 8px);min-width:76px;min-height:46px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;cursor:pointer;padding:7px 4px;}
.vz-seg button.vz-on{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.vz-seg button.vz-on.vz-neg{background:#dc2626;border-color:#dc2626;}
.vz-row-extra{margin-top:10px;display:flex;gap:8px;align-items:center;}
.vz-row-extra input{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.vz-photo-btn{flex:0 0 auto;min-width:50px;min-height:44px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);cursor:pointer;font-size:18px;}
.vz-photo-btn.vz-has-photo{border-color:#15803d;background:#e7f7ed;}
.vz-check-card{display:flex;align-items:center;gap:12px;cursor:pointer;}
.vz-check-box{flex:0 0 30px;height:30px;border:2px solid var(--border-color);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:19px;color:#fff;}
.vz-check-card.vz-on .vz-check-box{background:#15803d;border-color:#15803d;}
.vz-banner{border-radius:10px;padding:12px 14px;margin-bottom:10px;font-size:15px;line-height:1.45;}
.vz-banner-red{background:#fde8e8;color:#b91c1c;}
.vz-banner-blue{background:rgba(36,144,239,.1);color:var(--text-color);}
.vz-banner-green{background:#e7f7ed;color:#15803d;}
.vz-report-incident{margin-top:14px;text-align:center;font-size:14px;}
.vz-report-incident a{color:#b91c1c;text-decoration:underline;}
.vz-banner h6{margin:0 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;}
.vz-help{border:1px solid var(--border-color);border-radius:10px;margin-bottom:10px;overflow:hidden;background:var(--card-bg);}
.vz-help-head{display:flex;align-items:center;gap:8px;width:100%;padding:12px 14px;background:none;border:none;color:var(--text-color);font-size:15px;font-weight:600;cursor:pointer;text-align:left;}
.vz-help-caret{margin-left:auto;transition:transform .15s;}
.vz-help.vz-open .vz-help-caret{transform:rotate(90deg);}
.vz-help-body{display:none;padding:0 14px 12px;font-size:15px;line-height:1.5;color:var(--text-color);}
.vz-help.vz-open .vz-help-body{display:block;}
.vz-help-body img{max-width:100%;border-radius:8px;margin:10px 0 2px;display:block;}
.vz-help-cap{font-size:13px;color:var(--text-muted);margin-bottom:8px;}
.vz-location{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--text-muted);margin:2px 0 8px;}
.vz-location a{color:var(--primary,#2490ef);font-weight:600;white-space:nowrap;}
.vz-group-head{font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;margin:16px 0 8px;}
.vz-nav{position:fixed;left:0;right:0;bottom:0;background:var(--card-bg);border-top:1px solid var(--border-color);padding:10px 16px calc(10px + env(safe-area-inset-bottom));z-index:5;}
.vz-nav-inner{max-width:640px;margin:0 auto;display:flex;gap:10px;}
.vz-nav button{flex:1;min-height:52px;border-radius:10px;font-size:17px;font-weight:600;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);cursor:pointer;}
.vz-nav button.vz-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;}
.vz-nav button:disabled{opacity:.45;cursor:not-allowed;}
.vz-textarea{width:100%;min-height:110px;margin-top:8px;padding:11px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.vz-sig{width:100%;height:160px;border:1px dashed var(--border-color);border-radius:10px;background:var(--card-bg);touch-action:none;}
.vz-link-btn{border:none;background:none;color:var(--primary,#2490ef);font-size:15px;cursor:pointer;padding:6px 0;}
.vz-pick-card{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;cursor:pointer;color:var(--text-color);}
.vz-pick-card:active{border-color:var(--primary,#2490ef);}
.vz-section-head{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600;margin:16px 0 8px;}
.vz-up-card{display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:12px 14px;margin-bottom:10px;color:var(--text-color);}
.vz-up-info{min-width:0;}
.vz-up-date{font-size:13px;color:var(--text-muted);margin-top:3px;}
.vz-do-btn{flex:0 0 auto;min-height:44px;padding:0 15px;border-radius:8px;border:1px solid var(--primary,#2490ef);background:var(--primary,#2490ef);color:#fff;font-weight:600;font-size:15px;cursor:pointer;white-space:nowrap;}
.vz-do-btn:disabled{opacity:.5;cursor:not-allowed;}
.vz-log-btn{display:block;width:100%;min-height:48px;padding:12px 15px;border-radius:10px;border:1px dashed var(--border-color);background:transparent;color:var(--text-color);font-weight:600;font-size:15px;cursor:pointer;}
.vz-log-btn:active{border-color:var(--primary,#2490ef);}
.vz-log-hint{font-size:13px;color:var(--text-muted);margin:6px 2px 0;}
.vz-empty{text-align:center;color:var(--text-muted);padding:40px 10px;font-size:15px;}
.vz-done-screen{text-align:center;padding:48px 16px;}
.vz-done-screen .vz-done-icon{font-size:52px;}
.vz-readonly .vz-card,.vz-readonly .vz-nav{pointer-events:none;opacity:.75;}
`;

// Two kinds of authored text reach this page and they need opposite treatment.
//
// Text Editor fields — a Section's step_instructions, a Template's safety and
// wrap-up guidance — hold real HTML written in the Desk. `frappe.utils.xss_sanitise`
// escapes <, >, ", ' and / *by default* (its default strategies are ["html","js"]),
// so putting that HTML through it renders the markup as visible tags: the literal
// "<ul><li><b>" technicians were reading in the guidance panels. Parse it instead
// and strip only what could execute. DOMParser neither runs scripts nor fetches
// resources, so nothing in the stored string can fire while we clean it.
const VZ_STRIP_TAGS = "script,style,iframe,object,embed,link,meta,base,form,input,button";
const VZ_URL_ATTRS = ["href", "src", "xlink:href", "action", "formaction"];

function vz_rich_html(value) {
	const raw = String(value == null ? "" : value);
	if (!raw.trim()) return "";
	const doc = new DOMParser().parseFromString(raw, "text/html");
	doc.querySelectorAll(VZ_STRIP_TAGS).forEach((node) => node.remove());
	doc.querySelectorAll("*").forEach((node) => {
		Array.from(node.attributes).forEach((attr) => {
			const name = attr.name.toLowerCase();
			// Strip whitespace and control characters before testing the scheme —
			// "java\nscript:" and "java\tscript:" are still executed by browsers.
			const val = (attr.value || "").replace(/[\s\u0000-\u001f]/g, "").toLowerCase();
			if (name.startsWith("on")) {
				node.removeAttribute(attr.name);
			} else if (
				VZ_URL_ATTRS.includes(name) &&
				(val.startsWith("javascript:") || val.startsWith("data:text/html"))
			) {
				node.removeAttribute(attr.name);
			}
		});
	});
	return doc.body.innerHTML;
}

// Small Text fields — the Maintenance Profile's safety and wrap-up notes, a
// Serial No's site instructions — are plain text, so they stay escaped. But
// their line breaks carry the meaning (one hazard per line), and without this
// they collapse into a single run-on paragraph in the red safety banner.
// Which rows count as answered. This MIRRORS
// SapphireMaintenanceRecord._validate_mandatory_rows on the server, and the two
// must stay in step: if they drift, the wizard tells a technician the step is
// finished and then submit refuses it — by which point they are back at the truck.
// Consumables are exempt there and here; an untouched qty-0 dosing prefill is a
// legitimate "none used".
const VZ_ANSWERED = {
	maintenance_results: (row) => !!(row.selection || row.answer),
	chemistry_readings: (row) => !!parseFloat(row.reading_value),
	cleaning_tasks: (row) => !!(row.is_done || row.notes),
	consumables: () => true,
};

function vz_row_answered(table, row) {
	const test = VZ_ANSWERED[table];
	return test ? test(row) : true;
}

// A mandatory row says so on the card, and says whether it is still outstanding.
// Before this, is_mandatory was carried all the way from the template to the
// submit gate without ever being shown to the person filling the form in.
function vz_required_chip(table, row) {
	if (!row.is_mandatory) return "";
	return vz_row_answered(table, row)
		? `<span class="vz-chip vz-chip-green">&#10003;</span>`
		: `<span class="vz-chip vz-chip-req">${__("Required")}</span>`;
}

function vz_plain_html(value) {
	return frappe.utils.escape_html(String(value == null ? "" : value)).replace(/\n/g, "<br>");
}

class VisitWizard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		$("<style>").text(VZ_STYLE).appendTo(page.body);
		this.$wrap = $('<div class="vz-wrap"></div>').appendTo(page.body);
		this.page.set_secondary_action(__("Reload"), () => this.reload(), "refresh");
		// Closing the tab mid-visit used to lose whatever had not autosaved yet,
		// silently. Technicians work on phones and switch apps constantly.
		window.addEventListener("beforeunload", (event) => {
			if (this.doc && this.doc.docstatus === 0 && this.has_dirty()) {
				event.preventDefault();
				event.returnValue = "";
				return "";
			}
		});
		this.reset();
	}

	reset() {
		this.doc = null;
		this.dashboard = {};
		this.section_meta = {};
		this.template_meta = {};
		this.feature_names = {};
		this.steps = [];
		this.step_index = 0;
		this.features = [];
		this.feature = null;
		this.dirty = { fields: {}, rows: {} };
		this.pending_added = [];
		this.safety_ok = false;
		this._save_timer = null;
	}

	// ----- routing -------------------------------------------------------

	handle_route() {
		let record = frappe.utils.get_url_arg("record");
		if (!record && frappe.route_options && frappe.route_options.record) {
			record = frappe.route_options.record;
			frappe.route_options = null;
		}
		if (record && (!this.doc || this.doc.name !== record)) {
			this.load_record(record);
		} else if (!record && !this.doc) {
			this.show_picker();
		}
	}

	reload() {
		if (this.doc) {
			const name = this.doc.name;
			this.reset();
			this.load_record(name);
		} else {
			this.show_picker();
		}
	}

	// ----- picker --------------------------------------------------------

	show_picker() {
		this.page.set_title(__("Visits"));
		this.$wrap.html(`<div class="vz-empty">${__("Loading…")}</div>`);
		Promise.all([
			frappe.call("erpnext_enhancements.api.time_kiosk.get_my_visits_today"),
			frappe.call("erpnext_enhancements.api.maintenance_visit.get_upcoming_visits"),
		]).then(([today_res, upcoming_res]) => {
			const today = (today_res && today_res.message) || [];
			const upcoming = (upcoming_res && upcoming_res.message) || [];
			this.$wrap.empty();

			this.$wrap.append(`<div class="vz-section-head">${__("Today's Visits")}</div>`);
			if (today.length) {
				today.forEach((visit) => this.$wrap.append(this.today_card(visit)));
			} else {
				this.$wrap.append(
					`<div class="vz-muted" style="padding:2px 0 6px;">${__("Nothing scheduled for today.")}</div>`
				);
			}

			if (upcoming.length) {
				this.$wrap.append(`<div class="vz-section-head">${__("Upcoming — do one early")}</div>`);
				upcoming.forEach((visit) => this.$wrap.append(this.upcoming_card(visit)));
			}

			// Always offered, scheduled work or not: a tech standing at a
			// fountain with nothing due today still needs to be able to fill
			// the form in, and a form missed on Tuesday still needs writing up.
			this.$wrap.append(
				`<div class="vz-section-head">${__("Not on the list?")}</div>`
			);
			this.$wrap.append(this.log_visit_card());
		});
	}

	log_visit_card() {
		const $btn = $(`<button type="button" class="vz-log-btn">${__("+ Log a visit")}</button>`).on(
			"click",
			() => this.open_log_dialog()
		);
		return $("<div></div>")
			.append($btn)
			.append(
				`<div class="vz-log-hint">${__(
					"For a visit nobody scheduled, or to fill in the form for a day you missed."
				)}</div>`
			);
	}

	open_log_dialog() {
		frappe
			.call("erpnext_enhancements.api.maintenance_visit.get_loggable_sites")
			.then((r) => {
				const sites = (r && r.message) || [];
				if (!sites.length) {
					frappe.msgprint({
						title: __("Nothing to log against"),
						message: __(
							"No Active maintenance contract has covered water features yet, so there is no form to fill in. Ask the office to add the features to the contract."
						),
						indicator: "orange",
					});
					return;
				}
				const by_contract = {};
				sites.forEach((site) => {
					by_contract[site.contract] = site;
				});

				const dialog = new frappe.ui.Dialog({
					title: __("Log a visit"),
					fields: [
						{
							fieldname: "contract",
							fieldtype: "Select",
							label: __("Site"),
							reqd: 1,
							options: sites.map((site) => ({
								label: site.project_title,
								value: site.contract,
							})),
							default: sites[0].contract,
						},
						{
							fieldname: "serial_no",
							fieldtype: "Select",
							label: __("Water Feature"),
						},
						{
							fieldname: "visit_date",
							fieldtype: "Date",
							label: __("Date of visit"),
							reqd: 1,
							default: frappe.datetime.get_today(),
							description: __("Today, or the earlier day you are filling in for."),
						},
					],
					primary_action_label: __("Start form"),
					primary_action: (values) => {
						if (values.visit_date > frappe.datetime.get_today()) {
							frappe.msgprint(__("A visit cannot be logged for a future date."));
							return;
						}
						const site = by_contract[values.contract] || {};
						if (site.visit_shape !== "Per Site Visit" && !values.serial_no) {
							frappe.msgprint(__("Pick which water feature this visit covers."));
							return;
						}
						dialog.disable_primary_action();
						dialog.set_message(__("Creating…"));
						frappe
							.call({
								method: "erpnext_enhancements.api.maintenance_visit.create_visit",
								args: {
									contract: values.contract,
									serial_no:
										site.visit_shape === "Per Site Visit"
											? null
											: values.serial_no || null,
									visit_date: values.visit_date,
								},
							})
							.then((res) => {
								const name = res && res.message;
								if (!name) throw new Error("no record returned");
								if (site.open_draft && site.open_draft === name) {
									frappe.show_alert({
										message: __("This site already had an open form — opening that one."),
										indicator: "blue",
									});
								}
								dialog.hide();
								window.history.replaceState(
									null,
									"",
									`/app/visit-wizard?record=${encodeURIComponent(name)}`
								);
								this.load_record(name);
							})
							.catch(() => {
								dialog.clear_message();
								dialog.enable_primary_action();
							});
					},
				});

				// The feature picker only means anything on a Per Feature
				// contract — a Per Site Visit record covers every feature at
				// once, so offering a choice there would be a lie.
				const sync_feature = () => {
					const site = by_contract[dialog.get_value("contract")] || {};
					const per_site = site.visit_shape === "Per Site Visit";
					const features = site.features || [];
					dialog.set_df_property("serial_no", "hidden", per_site || !features.length);
					dialog.set_df_property("serial_no", "reqd", !per_site && features.length ? 1 : 0);
					dialog.set_df_property(
						"serial_no",
						"options",
						features.map((feature) => ({
							label: feature.item_name || feature.serial_no,
							value: feature.serial_no,
						}))
					);
					dialog.set_value("serial_no", features.length && !per_site ? features[0].serial_no : "");
				};
				dialog.fields_dict.contract.$input.on("change", sync_feature);
				sync_feature();
				dialog.show();
			});
	}

	today_card(visit) {
		const sub = visit.visit_label || visit.serial_no || __("Site visit");
		return $(`<button class="vz-pick-card">
				<div class="vz-card-title">${frappe.utils.escape_html(visit.project_title || visit.project)}</div>
				<div class="vz-card-sub">${frappe.utils.escape_html(sub)} · ${frappe.utils.escape_html(visit.name)}</div>
			</button>`).on("click", () => {
			window.history.replaceState(null, "", `/app/visit-wizard?record=${encodeURIComponent(visit.name)}`);
			this.load_record(visit.name);
		});
	}

	upcoming_card(visit) {
		const what = visit.item_name || visit.serial_no || __("Whole site");
		const when = visit.next_visit_date
			? frappe.datetime.str_to_user(visit.next_visit_date)
			: "";
		const due =
			visit.days_until != null
				? __("due {0} · in {1} days", [when, visit.days_until])
				: when;
		const $card = $(`<div class="vz-up-card">
				<div class="vz-up-info">
					<div class="vz-card-title">${frappe.utils.escape_html(visit.project_title || visit.project)}</div>
					<div class="vz-card-sub">${frappe.utils.escape_html(what)}</div>
					<div class="vz-up-date">${frappe.utils.escape_html(due)}</div>
				</div>
				<button type="button" class="vz-do-btn">${__("Do Visit Today")}</button>
			</div>`);
		$card.find(".vz-do-btn").on("click", (event) => {
			const $btn = $(event.currentTarget).prop("disabled", true).text(__("Creating…"));
			frappe
				.call({
					method: "erpnext_enhancements.api.maintenance_visit.create_visit_today",
					args: { contract: visit.contract, serial_no: visit.serial_no || null },
				})
				.then((r) => {
					const name = r && r.message;
					if (!name) throw new Error("no record returned");
					window.history.replaceState(
						null,
						"",
						`/app/visit-wizard?record=${encodeURIComponent(name)}`
					);
					this.load_record(name);
				})
				.catch(() => {
					$btn.prop("disabled", false).text(__("Do Visit Today"));
				});
		});
		return $card;
	}

	// ----- loading -------------------------------------------------------

	load_record(name) {
		this.reset();
		this.$wrap.html(`<div class="vz-empty">${__("Loading visit…")}</div>`);
		frappe
			.call({
				method: "erpnext_enhancements.api.maintenance_visit.get_visit_bootstrap",
				args: { record: name },
			})
			.then((r) => {
				const data = r.message || {};
				this.doc = data.record;
				this.dashboard = data.dashboard || {};
				this.section_meta = data.sections || {};
				this.template_meta = data.template_meta || {};
				this.feature_names = data.features || {};
				this.apply_state(data.state);
				this.safety_ok = !!this.doc.safety_acknowledged;
				this.build_steps();
				this.build_features();
				this.render();
			})
			.catch(() => {
				this.$wrap.html(`<div class="vz-empty">${__("Could not load this visit.")}</div>`);
			});
	}

	apply_state(state) {
		if (!state) return;
		this.doc.modified = state.modified;
		this.doc.docstatus = state.docstatus;
		this.doc.workflow_state = state.workflow_state;
		this.doc.completion_percent = state.completion_percent;
		const flags = {};
		(state.readings || []).forEach((row) => (flags[row.name] = row.out_of_range));
		(this.doc.chemistry_readings || []).forEach((row) => {
			if (row.name in flags) row.out_of_range = flags[row.name];
		});
		const pct = state.completion_percent || 0;
		this.page.set_indicator(
			__("{0}% complete", [pct]),
			pct >= 100 ? "green" : pct >= 50 ? "orange" : "red"
		);
	}

	build_steps() {
		this.steps = [{ key: "safety", title: __("Safety") }];
		if ((this.doc.chemistry_readings || []).length) {
			this.steps.push({ key: "readings", title: __("Water Chemistry"), table: "chemistry_readings" });
		}
		if ((this.doc.consumables || []).length) {
			this.steps.push({ key: "consumables", title: __("Chemicals Used"), table: "consumables" });
		}
		if ((this.doc.maintenance_results || []).length) {
			this.steps.push({ key: "results", title: __("Inspection"), table: "maintenance_results" });
		}
		if ((this.doc.cleaning_tasks || []).length) {
			this.steps.push({ key: "tasks", title: __("Cleaning"), table: "cleaning_tasks" });
		}
		this.steps.push({ key: "wrapup", title: __("Wrap-up") });
	}

	build_features() {
		// Per Site Visit records tag every row with its water feature; one
		// header serial (or untagged rows) means no tab strip.
		const serials = new Set();
		["chemistry_readings", "consumables", "maintenance_results", "cleaning_tasks"].forEach((table) => {
			(this.doc[table] || []).forEach((row) => row.serial_no && serials.add(row.serial_no));
		});
		this.features = !this.doc.serial_no && serials.size > 1 ? Array.from(serials) : [];
		this.feature = this.features[0] || null;
	}

	rows(table) {
		const rows = this.doc[table] || [];
		return this.features.length ? rows.filter((row) => row.serial_no === this.feature) : rows;
	}

	// ----- dirty tracking + autosave --------------------------------------

	set_field(field, value) {
		this.doc[field] = value;
		this.dirty.fields[field] = value;
		this.schedule_save();
	}

	set_row(table, row, field, value) {
		row[field] = value;
		if (row.name) {
			const table_dirty = (this.dirty.rows[table] = this.dirty.rows[table] || {});
			(table_dirty[row.name] = table_dirty[row.name] || {})[field] = value;
		}
		this.schedule_save();
	}

	schedule_save() {
		clearTimeout(this._save_timer);
		this._save_timer = setTimeout(() => this.flush_save(), 4000);
		this.set_save_state("pending");
	}

	// Autosave used to be entirely silent, including when it failed: the patch
	// was re-queued and the error rethrown into nothing, so a technician on a
	// bad signal could fill in a whole visit believing it was saved. Now the
	// state is always on screen, and a failure retries on its own.
	set_save_state(state) {
		this._save_state = state;
		if (!this.$wrap) return;
		let $el = this.$wrap.find(".vz-savestate");
		if (!$el.length) {
			$el = $('<div class="vz-savestate"></div>').appendTo(this.$wrap);
		}
		const labels = {
			pending: __("Unsaved changes"),
			saving: __("Saving..."),
			saved: __("Saved"),
			error: __("NOT SAVED - retrying"),
		};
		$el.toggleClass("vz-err", state === "error");
		if (!labels[state]) {
			$el.empty();
			return;
		}
		$el.html(`<span>${labels[state]}</span>`);
		if (state === "saved") {
			clearTimeout(this._saved_timer);
			this._saved_timer = setTimeout(() => {
				if (this._save_state === "saved") this.set_save_state("idle");
			}, 2000);
		}
	}

	// Failed saves used to sit in this.dirty until the technician happened to
	// edit something else. Retry on a backoff instead, so a signal that comes
	// back flushes the work without anyone noticing it had gone.
	schedule_retry() {
		clearTimeout(this._retry_timer);
		this._retry_attempt = Math.min((this._retry_attempt || 0) + 1, 5);
		const delay = 1000 * Math.pow(2, this._retry_attempt);
		this._retry_timer = setTimeout(() => {
			if (this.has_dirty()) this.flush_save().catch(() => {});
		}, delay);
	}

	has_dirty() {
		return (
			Object.keys(this.dirty.fields).length ||
			Object.keys(this.dirty.rows).length ||
			this.pending_added.length
		);
	}

	flush_save() {
		clearTimeout(this._save_timer);
		if (!this.doc || this.doc.docstatus !== 0 || !this.has_dirty()) {
			return Promise.resolve();
		}
		const rows = {};
		Object.entries(this.dirty.rows).forEach(([table, by_name]) => {
			rows[table] = Object.entries(by_name).map(([name, changes]) => ({ name, ...changes }));
		});
		this.pending_added.forEach((row) => {
			(rows.consumables = rows.consumables || []).push({
				item: row.item,
				qty: row.qty,
				warehouse: row.warehouse,
			});
		});
		const added_local = this.pending_added;
		const sent_dirty = this.dirty;
		const patch = { fields: sent_dirty.fields, rows };
		this.dirty = { fields: {}, rows: {} };
		this.pending_added = [];
		this.set_save_state("saving");

		return frappe
			.call({
				method: "erpnext_enhancements.api.maintenance_visit.save_visit",
				args: {
					record: this.doc.name,
					patch: JSON.stringify(patch),
					modified: this.doc.modified,
				},
			})
			.then((r) => {
				const state = r.message || {};
				((state.added || {}).consumables || []).forEach((name, index) => {
					if (added_local[index]) added_local[index].name = name;
				});
				this.apply_state(state);
				this.refresh_reading_flags();
				this._retry_attempt = 0;
				this.set_save_state(this.has_dirty() ? "pending" : "saved");
			})
			.catch((error) => {
				// keep the edits — newer in-flight changes win over the failed batch
				const merged = { fields: { ...sent_dirty.fields, ...this.dirty.fields }, rows: sent_dirty.rows };
				Object.entries(this.dirty.rows).forEach(([table, by_name]) => {
					const base = (merged.rows[table] = merged.rows[table] || {});
					Object.entries(by_name).forEach(([name, changes]) => {
						base[name] = { ...(base[name] || {}), ...changes };
					});
				});
				this.dirty = merged;
				this.pending_added = added_local.concat(this.pending_added);
				this.set_save_state("error");
				this.schedule_retry();
				throw error;
			});
	}

	refresh_reading_flags() {
		this.$wrap.find("[data-reading-row]").each((_, el) => {
			const $card = $(el);
			const row = (this.doc.chemistry_readings || []).find(
				(reading) => reading.name === $card.attr("data-reading-row")
			);
			if (!row) return;
			$card.toggleClass("vz-bad", !!row.out_of_range);
			$card.find(".vz-range-chip").toggleClass("vz-chip-red", !!row.out_of_range);
		});
	}

	// ----- rendering -------------------------------------------------------

	render() {
		const step = this.steps[this.step_index];
		const readonly = this.doc.docstatus !== 0;
		this.page.set_title(
			frappe.utils.escape_html(
				this.doc.visit_label || this.doc.serial_no || this.doc.project || this.doc.name
			)
		);
		this.$wrap.empty().toggleClass("vz-readonly", readonly);

		if (readonly) {
			this.$wrap.append(
				`<div class="vz-banner vz-banner-green">${__("This visit is submitted — read-only view. Use the desk form for details.")}</div>`
			);
		} else if (this.doc.workflow_state === "Pending Review") {
			this.$wrap.append(
				`<div class="vz-banner vz-banner-blue">${__("This visit is pending review.")}</div>`
			);
		}

		const pct = this.doc.completion_percent || 0;
		const left = this.step_outstanding(step);
		this.$wrap.append(`
			<div class="vz-stepline">
				<span>${__("Step {0} of {1}", [this.step_index + 1, this.steps.length])} · ${frappe.utils.escape_html(step.title)}</span>
				<span>${left ? `<span class="vz-chip vz-chip-req">${__("{0} required left", [left])}</span> ` : ""}${pct}%</span>
			</div>
			<div class="vz-progress"><div style="width:${pct}%"></div></div>
		`);

		// One bar per step, coloured by whether that step still owes a required
		// answer. The percentage alone never said WHICH step was unfinished, so
		// the first a technician heard of it was submit refusing at the truck.
		const $dots = $('<div class="vz-step-dots"></div>');
		this.steps.forEach((candidate, index) => {
			const outstanding = this.step_outstanding(candidate);
			const cls = index === this.step_index ? "vz-here" : outstanding ? "vz-todo" : "vz-done";
			$(`<button type="button" class="vz-step-dot ${cls}"></button>`)
				.attr(
					"title",
					outstanding
						? `${candidate.title} — ${__("{0} required left", [outstanding])}`
						: candidate.title
				)
				.attr("aria-label", candidate.title)
				.on("click", () => this.go(index))
				.appendTo($dots);
		});
		this.$wrap.append($dots);

		if (this.features.length && step.table) {
			this.render_feature_tabs();
		}

		const renderers = {
			safety: () => this.render_safety(),
			readings: () => this.render_readings(),
			consumables: () => this.render_consumables(),
			results: () => this.render_results(),
			tasks: () => this.render_tasks(),
			wrapup: () => this.render_wrapup(),
		};
		renderers[step.key]();

		this.render_nav();
		// render() empties the wrapper, so the save indicator has to be put back
		// or a pending/failed save goes quiet the moment you change step.
		if (this._save_state) this.set_save_state(this._save_state);
	}

	// Real buttons, not clickable divs — a tab strip you cannot reach from the
	// keyboard is unusable with an accessibility switch or an external keyboard.
	// Labels come from the Serial No's item_name (see api _feature_names); the
	// docname is the fallback and reads badly on a phone-width strip.
	render_feature_tabs() {
		const $tabs = $('<div class="vz-tabs" role="tablist"></div>').appendTo(this.$wrap);
		const table = this.steps[this.step_index].table;
		this.features.forEach((serial) => {
			const active = serial === this.feature;
			const label = (this.feature_names || {})[serial] || serial;
			// Outstanding required rows for this feature on this step, so a tab
			// that still needs work says so before you leave the site.
			const left = (this.doc[table] || []).filter(
				(row) => row.serial_no === serial && row.is_mandatory && !vz_row_answered(table, row)
			).length;
			$(`<button type="button" role="tab" aria-selected="${active}" class="vz-tab ${active ? "vz-active" : ""}">
					${frappe.utils.escape_html(label)}${left ? ` <span class="vz-chip vz-chip-req">${left}</span>` : ""}
				</button>`)
				.on("click", () => {
					this.flush_save().catch(() => {});
					this.feature = serial;
					this.render();
				})
				.appendTo($tabs);
		});
	}

	// Required rows still unanswered on a step, counted across ALL features
	// rather than just the visible tab — a Per Site Visit record can be finished
	// on the fountain in front of you and still owe answers on the other one.
	step_outstanding(step) {
		if (!step.table) return 0;
		return (this.doc[step.table] || []).filter(
			(row) => row.is_mandatory && !vz_row_answered(step.table, row)
		).length;
	}

	render_nav() {
		this.$wrap.find(".vz-nav").remove();
		const last = this.step_index === this.steps.length - 1;
		const $nav = $('<div class="vz-nav"><div class="vz-nav-inner"></div></div>');
		const $inner = $nav.find(".vz-nav-inner");

		if (this.step_index > 0) {
			$('<button type="button"></button>')
				.text(__("Back"))
				.on("click", () => this.go(this.step_index - 1))
				.appendTo($inner);
		}
		if (!last) {
			const on_safety = this.steps[this.step_index].key === "safety";
			const $next = $('<button type="button" class="vz-primary"></button>')
				.text(on_safety ? __("Start Visit") : __("Next"))
				.on("click", () => this.go(this.step_index + 1))
				.appendTo($inner);
			if (on_safety && !this.safety_ok) {
				$next.prop("disabled", true);
			}
		} else if (this.doc.docstatus === 0) {
			const finishing_label =
				this.doc.workflow_state === "Pending Review" ? __("Approve & Submit") : __("Finish Visit");
			$('<button type="button" class="vz-primary"></button>')
				.text(finishing_label)
				.on("click", () => this.finish())
				.appendTo($inner);
		}
		this.$wrap.append($nav);
	}

	go(index) {
		this.flush_save();
		this.step_index = Math.max(0, Math.min(index, this.steps.length - 1));
		this.render();
		window.scrollTo(0, 0);
	}

	// Render a section-backed step: group the step's rows by their source
	// Section (first-seen order), leading each group with its 📍 location line
	// and collapsible how-to panel. A sub-header is shown only when the step
	// draws from more than one section.
	render_step_groups(table, render_row) {
		const rows = this.rows(table);
		const order = [];
		const groups = {};
		rows.forEach((row) => {
			const key = row.section || row.section_title || "__";
			if (!groups[key]) {
				groups[key] = [];
				order.push(key);
			}
			groups[key].push(row);
		});
		const multi = order.length > 1;
		order.forEach((key) => {
			const meta = this.section_meta[key];
			const title = (meta && meta.title) || groups[key][0].section_title || "";
			if (multi && title) {
				this.$wrap.append(`<div class="vz-group-head">${frappe.utils.escape_html(title)}</div>`);
			}
			if (meta && meta.location) {
				this.render_location(meta.location);
			}
			this.render_help(meta);
			groups[key].forEach((row) => this.$wrap.append(render_row(row)));
		});
	}

	// Always-visible "where on the property" line for a step: 📍 note plus a
	// tap-to-navigate Map link when the template step carries coordinates.
	render_location(location) {
		let html = `<span>📍 ${frappe.utils.escape_html(location.note || __("Step location"))}</span>`;
		if (location.latitude && location.longitude) {
			const url = `https://www.google.com/maps?q=${encodeURIComponent(location.latitude + "," + location.longitude)}`;
			html += `<a href="${url}" target="_blank" rel="noopener">${__("Map")} ↗</a>`;
		}
		this.$wrap.append(`<div class="vz-location">${html}</div>`);
	}

	// Collapsible (collapsed-by-default) guidance panel: sanitized instructions
	// HTML, the step's location photo (captioned with its note), and any how-to
	// images with captions. `title` defaults to "How to do this".
	render_help(meta, title) {
		if (!meta) return;
		const location = meta.location || {};
		const images = [];
		if (location.photo) {
			images.push({ image: location.photo, caption: location.note || __("Step location") });
		}
		(meta.images || []).forEach((img) => images.push(img));

		const has_text = meta.instructions && String(meta.instructions).trim();
		if (!has_text && !images.length) return;

		let body = has_text ? `<div>${vz_rich_html(meta.instructions)}</div>` : "";
		images.forEach((img) => {
			if (!img.image) return;
			body += `<img src="${encodeURI(img.image)}" alt="" loading="lazy">`;
			if (img.caption) {
				body += `<div class="vz-help-cap">${frappe.utils.escape_html(img.caption)}</div>`;
			}
		});

		const $help = $(`
			<div class="vz-help">
				<button type="button" class="vz-help-head">
					<span>ℹ️ ${frappe.utils.escape_html(title || __("How to do this"))}</span>
					<span class="vz-help-caret">▸</span>
				</button>
				<div class="vz-help-body">${body}</div>
			</div>
		`);
		$help.find(".vz-help-head").on("click", () => $help.toggleClass("vz-open"));
		this.$wrap.append($help);
	}

	// ----- step: safety ----------------------------------------------------

	render_safety() {
		const profile = this.dashboard.profile || {};
		const serial = this.dashboard.serial_no || {};
		const contract = this.dashboard.contract || {};

		// These are Small Text / Data fields, so they stay escaped — but through
		// vz_plain_html, which keeps the line breaks. One hazard per line is how
		// these notes are written, and collapsing them into a single paragraph is
		// how a technician skims past the one that mattered.
		// "N/A" is not information. The banner used to print every field it had,
		// so a site with no gate and no key rendered "Code: N/A" and "Key: N/A",
		// training technicians to skim past the panel that also carries the
		// things that will hurt them.
		const useful = (value) => {
			const text = String(value == null ? "" : value).trim();
			return text && !/^(n\/?a\.?|none|nil|-{1,2})$/i.test(text);
		};
		const access = [];
		const code = profile.access_codes || contract.gate_code;
		if (useful(code)) {
			// A value that already labels itself ("Gate code: 2244 - Key: N/A")
			// does not want a second "Code:" bolted on the front.
			access.push(
				String(code).includes(":")
					? vz_plain_html(code)
					: `${__("Code")}: <b>${vz_plain_html(code)}</b>`
			);
		}
		if (useful(contract.key_location)) {
			access.push(`${__("Key")}: ${vz_plain_html(contract.key_location)}`);
		}
		if (useful(serial.custom_site_instructions)) {
			access.push(vz_plain_html(serial.custom_site_instructions));
		}

		this.$wrap.append(`
			<div class="vz-banner vz-banner-red">
				<h6>${__("Safety Instructions")}</h6>
				${vz_plain_html(profile.safety_instructions || __("No specific safety instructions provided."))}
			</div>
			${
				access.length
					? `<div class="vz-banner vz-banner-blue">
				<h6>${__("Access & Site")}</h6>
				${access.join("<br>")}
			</div>`
					: ""
			}
		`);

		// Visit-type safety guidance from the form template (the site-specific
		// text above stays prominent in the red banner, never collapsed).
		this.render_help(this.template_meta.safety, __("Before you start"));

		const $ack = $(`
			<div class="vz-card vz-check-card ${this.safety_ok ? "vz-on" : ""}">
				<div class="vz-check-box">${this.safety_ok ? "✓" : ""}</div>
				<div>
					<div class="vz-card-title">${__("Safety Procedures & PPE Acknowledged")}</div>
					<div class="vz-card-sub">${__("Required before the checklist opens.")}</div>
				</div>
			</div>
		`).on("click", () => {
			this.safety_ok = !this.safety_ok;
			this.set_field("safety_acknowledged", this.safety_ok ? 1 : 0);
			$ack.toggleClass("vz-on", this.safety_ok);
			$ack.find(".vz-check-box").text(this.safety_ok ? "✓" : "");
			this.$wrap.find(".vz-nav .vz-primary").prop("disabled", !this.safety_ok);
		});
		this.$wrap.append($ack);
		this.render_report_incident();
	}

	// Reporting an injury from where it happened.
	//
	// On the safety step, because that is the screen a technician is already on
	// with a phone in their hand, and because a feature reachable only from its own
	// Desk list is a feature nobody finds when they are hurt. If filing is
	// expensive it does not happen, and a log that looks clean because nobody could
	// face filling it in is worse than no log.
	//
	// Quiet, not prominent: it sits under the acknowledgement as a line of text
	// rather than a button competing with the checklist. The people who need it
	// will look for it.
	render_report_incident() {
		const $link = $(`
			<div class="vz-report-incident">
				<a href="#">${__("Report an injury or a near miss")}</a>
			</div>
		`).on("click", "a", (e) => {
			e.preventDefault();
			this.open_incident_dialog();
		});
		this.$wrap.append($link);
		this.render_lone_work();
	}

	// Going to this one alone.
	//
	// Same screen and the same reasoning as reporting an incident: this is where a
	// technician is standing with a phone before they start, and a check-in that
	// lives anywhere else is a check-in that does not happen. Four fields, one of
	// which is prefilled from the visit.
	render_lone_work() {
		const $link = $(`
			<div class="vz-report-incident">
				<a href="#" data-lone="1">${__("I am here on my own")}</a>
			</div>
		`).on("click", "a", (e) => {
			e.preventDefault();
			this.open_lone_work_dialog();
		});
		this.$wrap.append($link);
	}

	open_lone_work_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Working alone"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "where",
					label: __("Where"),
					reqd: 1,
					default: (this.doc && this.doc.customer) || "",
					description: __("Enough for somebody to drive to."),
				},
				{
					fieldtype: "Datetime",
					fieldname: "expected_out_by",
					label: __("Out by"),
					reqd: 1,
					default: frappe.datetime.add_minutes(frappe.datetime.now_datetime(), 90),
					description: __("If this passes without a check-out we chase you, then your supervisor, then the office."),
				},
			],
			primary_action_label: __("Start"),
			primary_action: (values) => {
				dialog.hide();
				frappe.call({
					method: "erpnext_enhancements.hr_enhancements.lonework.start_session",
					args: Object.assign({}, values, {
						customer: this.doc && this.doc.customer,
						maintenance_record: this.doc && this.doc.name,
					}),
					freeze: true,
					callback: (r) => {
						if (!r || !r.message) return;
						frappe.show_alert({
							message: __("Checked in. Remember to check out."),
							indicator: "green",
						});
					},
				});
			},
		});
		dialog.show();
	}

	open_incident_dialog() {
		// Five fields. Everything else on the record -- treatment, classification,
		// body part, root cause -- is a judgement somebody makes later, and asking
		// for it here is asking a person in pain to classify their own injury.
		const dialog = new frappe.ui.Dialog({
			title: __("Report it"),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "incident_type",
					label: __("What kind"),
					options: ["Injury", "Near Miss", "Illness", "Property Damage"].join("\n"),
					default: "Injury",
					reqd: 1,
				},
				{
					fieldtype: "Small Text",
					fieldname: "what_happened",
					label: __("What happened"),
					reqd: 1,
				},
				{
					fieldtype: "Data",
					fieldname: "location_text",
					label: __("Where exactly"),
					reqd: 1,
					default: __("On site"),
					description: __("“Pump vault, north basin” — the part of the site, not just the address."),
				},
				{
					fieldtype: "Small Text",
					fieldname: "doing_before",
					label: __("What you were doing just before"),
					description: __("If you can. It can be filled in later."),
				},
			],
			primary_action_label: __("Send it"),
			primary_action: (values) => {
				dialog.hide();
				frappe.call({
					method: "erpnext_enhancements.hr_enhancements.safety.report_incident",
					args: Object.assign({}, values, {
						// `this.doc.name`, not `this.docname` -- the wizard holds the
						// loaded record on `this.doc` (see line ~491) and has no
						// `docname`. An undefined here would silently file the incident
						// with no link back to the visit it happened on.
						maintenance_record: this.doc && this.doc.name,
					}),
					freeze: true,
					freeze_message: __("Sending…"),
					callback: (r) => {
						if (!r || !r.message) return;
						// The reportable case says so immediately. The clock starts when the
						// company learns of it, and that is now.
						if (r.message.reportable && r.message.reportable !== __("No")) {
							frappe.msgprint({
								title: __("This one has to be reported"),
								indicator: "red",
								message: __(
									"<b>{0}</b>. Somebody must call UOSH by <b>{1}</b>, and the equipment must not be moved until they release the scene. HR has been emailed.",
									[r.message.reportable, r.message.due_by || ""]
								),
							});
							return;
						}
						frappe.show_alert({
							message: __("Reported. {0} has it.", [r.message.name]),
							indicator: "green",
						});
					},
				});
			},
		});
		dialog.show();
	}

	// ----- step: water chemistry --------------------------------------------

	render_readings() {
		this.render_step_groups("chemistry_readings", (row) => this.reading_card(row));
	}

	reading_card(row) {
		const range_label =
			row.min_value || row.max_value
				? `${row.min_value || 0} – ${row.max_value || "∞"} ${frappe.utils.escape_html(row.uom || "")}`
				: frappe.utils.escape_html(row.uom || "");
		const $card = $(`
			<div class="vz-card ${row.out_of_range ? "vz-bad" : ""}" data-reading-row="${frappe.utils.escape_html(row.name || "")}">
				<span class="vz-card-title">${frappe.utils.escape_html(row.reading || "")}</span>
				<span class="vz-req-slot">${vz_required_chip("chemistry_readings", row)}</span>
				<span class="vz-chip vz-range-chip ${row.out_of_range ? "vz-chip-red" : ""}">${range_label}</span>
				<input class="vz-num" type="number" inputmode="decimal" step="any"
					placeholder="—" value="${row.reading_value || ""}">
				<div class="vz-row-extra">
					<input type="text" placeholder="${__("Notes")}" value="${frappe.utils.escape_html(row.notes || "")}">
					<button type="button" class="vz-photo-btn ${row.photo ? "vz-has-photo" : ""}">📷</button>
				</div>
			</div>
		`);
		$card.find(".vz-num").on("change", (event) => {
			const value = parseFloat(event.target.value) || 0;
			this.set_row("chemistry_readings", row, "reading_value", value);
			$card.find(".vz-req-slot").html(vz_required_chip("chemistry_readings", row));
			// immediate local range hint; the server's verdict lands on save
			const low = row.min_value || 0;
			const high = row.max_value || 0;
			const out = value && ((low && value < low) || (high && value > high));
			$card.toggleClass("vz-bad", !!out);
		});
		$card.find(".vz-row-extra input").on("change", (event) => {
			this.set_row("chemistry_readings", row, "notes", event.target.value);
		});
		this.bind_photo($card.find(".vz-photo-btn"), "chemistry_readings", row);
		return $card;
	}

	// ----- step: chemicals used ----------------------------------------------

	render_consumables() {
		this.render_step_groups("consumables", (row) => this.consumable_card(row));

		if (this.doc.docstatus === 0) {
			$(`<button type="button" class="vz-link-btn">+ ${__("Add another item")}</button>`)
				.on("click", () => this.add_consumable())
				.appendTo(this.$wrap);
		}
	}

	consumable_card(row) {
		const title = row.item_name || row.item || "";
		const unit = row.uom ? ` (${row.uom})` : "";
		const $card = $(`
			<div class="vz-card ${row.qty ? "vz-done" : ""}">
				<div class="vz-card-title">${frappe.utils.escape_html(title)}${frappe.utils.escape_html(unit)}</div>
				${row.default_qty ? `<div class="vz-card-sub">${__("Usually {0}", [row.default_qty])}</div>` : ""}
				<div class="vz-stepper">
					<button type="button" class="vz-step-btn" data-dir="-1">−</button>
					<div class="vz-qty">${row.qty || 0}</div>
					<button type="button" class="vz-step-btn" data-dir="1">+</button>
				</div>
			</div>
		`);
		const step = row.qty_step || 1;
		const set_qty = (qty) => {
			qty = Math.max(0, Math.round(qty * 1000) / 1000);
			this.set_row("consumables", row, "qty", qty);
			$card.find(".vz-qty").text(qty);
			$card.toggleClass("vz-done", !!qty);
		};
		$card.find(".vz-step-btn").on("click", (event) => {
			const dir = parseInt($(event.currentTarget).attr("data-dir"), 10);
			// first + on an untouched row jumps straight to the usual dose
			if (dir > 0 && !row.qty && row.default_qty) {
				set_qty(row.default_qty);
			} else {
				set_qty((row.qty || 0) + dir * step);
			}
		});
		$card.find(".vz-qty").on("click", (event) => {
			const $qty = $(event.currentTarget);
			if ($qty.find("input").length) return;
			const $input = $(
				`<input type="number" inputmode="decimal" step="any" value="${row.qty || ""}">`
			);
			$qty.empty().append($input);
			$input.trigger("focus");
			$input.on("blur change", () => set_qty(parseFloat($input.val()) || 0));
		});
		return $card;
	}

	add_consumable() {
		const dialog = new frappe.ui.Dialog({
			title: __("Add Item"),
			fields: [
				{
					fieldname: "item",
					fieldtype: "Link",
					label: __("Item"),
					options: "Item",
					reqd: 1,
					get_query: () => ({ filters: { is_stock_item: 1 } }),
				},
				{ fieldname: "qty", fieldtype: "Float", label: __("Qty"), reqd: 1, default: 1 },
			],
			primary_action_label: __("Add"),
			primary_action: (values) => {
				dialog.hide();
				const row = {
					item: values.item,
					item_name: values.item,
					qty: values.qty,
					qty_step: 1,
					serial_no: this.feature,
				};
				this.doc.consumables.push(row);
				this.pending_added.push(row);
				this.flush_save().then(() => this.render());
			},
		});
		dialog.show();
	}

	// ----- step: inspection -----------------------------------------------

	render_results() {
		this.render_step_groups("maintenance_results", (row) => this.result_card(row));
	}

	result_card(row) {
		const options = (row.options || "").split("\n").map((option) => option.trim()).filter(Boolean);
		const choices = options.length ? options : ["Pass", "Fail", "Replace", "Other"];
		const $card = $(`
			<div class="vz-card ${row.selection || row.answer ? "vz-done" : ""}">
				<div class="vz-card-title">${frappe.utils.escape_html(row.question || "")} <span class="vz-req-slot">${vz_required_chip("maintenance_results", row)}</span></div>
				<div class="vz-seg"></div>
				<div class="vz-row-extra" style="display:none;">
					<input type="text" placeholder="${__("Details")}" value="">
					<button type="button" class="vz-photo-btn ${row.photo ? "vz-has-photo" : ""}">📷</button>
				</div>
			</div>
		`);
		const $seg = $card.find(".vz-seg");
		const $extra = $card.find(".vz-row-extra");
		const $detail = $extra.find("input");
		const negative = (choice) => ["Fail", "Replace"].includes(choice);

		const sync_extra = () => {
			const show = row.selection && (negative(row.selection) || row.selection === "Other");
			$extra.toggle(!!show || !!row.answer || !!row.other_details);
			$detail.attr(
				"placeholder",
				row.selection === "Other" ? __("What happened? (required)") : __("Notes")
			);
			$detail.val(row.selection === "Other" ? row.other_details || "" : row.answer || "");
		};

		choices.forEach((choice) => {
			const $btn = $(`<button type="button">${frappe.utils.escape_html(choice)}</button>`);
			$btn.toggleClass("vz-on", row.selection === choice);
			$btn.toggleClass("vz-neg", row.selection === choice && negative(choice));
			$btn.on("click", () => {
				this.set_row("maintenance_results", row, "selection", row.selection === choice ? "" : choice);
				$seg.find("button").removeClass("vz-on vz-neg");
				if (row.selection) {
					$btn.addClass("vz-on");
					if (negative(choice)) $btn.addClass("vz-neg");
				}
				$card.toggleClass("vz-done", !!(row.selection || row.answer));
				$card.find(".vz-req-slot").html(vz_required_chip("maintenance_results", row));
				sync_extra();
			});
			$seg.append($btn);
		});

		$detail.on("change", (event) => {
			const field = row.selection === "Other" ? "other_details" : "answer";
			this.set_row("maintenance_results", row, field, event.target.value);
		});
		this.bind_photo($extra.find(".vz-photo-btn"), "maintenance_results", row);
		sync_extra();
		return $card;
	}

	// ----- step: cleaning ---------------------------------------------------

	render_tasks() {
		this.render_step_groups("cleaning_tasks", (row) => this.task_card(row));
	}

	task_card(row) {
		const $card = $(`
			<div class="vz-card vz-check-card ${row.is_done ? "vz-on vz-done" : ""}">
				<div class="vz-check-box">${row.is_done ? "✓" : ""}</div>
				<div style="flex:1;">
					<div class="vz-card-title">${frappe.utils.escape_html(row.task || "")} <span class="vz-req-slot">${vz_required_chip("cleaning_tasks", row)}</span></div>
					${row.notes ? `<div class="vz-card-sub">${frappe.utils.escape_html(row.notes)}</div>` : ""}
				</div>
			</div>
		`).on("click", () => {
			const done = row.is_done ? 0 : 1;
			this.set_row("cleaning_tasks", row, "is_done", done);
			$card.toggleClass("vz-on vz-done", !!done);
			$card.find(".vz-check-box").text(done ? "✓" : "");
			$card.find(".vz-req-slot").html(vz_required_chip("cleaning_tasks", row));
		});
		return $card;
	}

	// ----- step: wrap-up ------------------------------------------------------

	render_wrapup() {
		// Template wrap-up guidance + the site's own reminders (Maintenance
		// Profile), stacked into one collapsible panel.
		const template_wrapup = this.template_meta.wrapup || {};
		const site_note = (this.dashboard.profile || {}).wrapup_instructions;
		const wrapup_meta = {
			// The template's half is Text Editor HTML and the site note is plain
			// Small Text, so each is prepared for its own type here and the result
			// goes through render_help as ready markup. Escaping the whole thing
			// downstream is what turned this panel's own <p> tags into visible text.
			instructions:
				(template_wrapup.instructions || "") +
				(site_note ? `<p><b>${__("This site")}:</b> ${vz_plain_html(site_note)}</p>` : ""),
			images: template_wrapup.images || [],
		};
		this.render_help(wrapup_meta, __("Wrapping up"));

		const $notes_card = $(`
			<div class="vz-card">
				<div class="vz-card-title">${__("Visit Notes")}</div>
				<textarea class="vz-textarea" placeholder="${__("Anything worth recording…")}">${frappe.utils.escape_html(this.doc.visit_notes || "")}</textarea>
				<button type="button" class="vz-link-btn vz-dictate" style="display:none;">🎤 ${__("Dictate")}</button>
			</div>
		`);
		$notes_card.find("textarea").on("change", (event) => {
			this.set_field("visit_notes", event.target.value);
		});
		this.setup_dictation($notes_card);
		this.$wrap.append($notes_card);

		const $sig_card = $(`
			<div class="vz-card">
				<div class="vz-card-title">${__("Client Sign-off")}</div>
				<div class="vz-card-sub">${__("Optional — have the client sign below.")}</div>
				<canvas class="vz-sig"></canvas>
				<button type="button" class="vz-link-btn">${__("Clear signature")}</button>
			</div>
		`);
		this.setup_signature($sig_card);
		this.$wrap.append($sig_card);
	}

	setup_dictation($card) {
		const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
		if (!Recognition || this.doc.docstatus !== 0) return;
		const $btn = $card.find(".vz-dictate").show();
		$btn.on("click", () => {
			if (this._recognition) {
				this._recognition.stop();
				return;
			}
			const recognition = new Recognition();
			this._recognition = recognition;
			recognition.lang = frappe.boot.lang || "en-US";
			recognition.interimResults = false;
			frappe.show_alert({ message: __("Listening… tap again to stop."), indicator: "blue" });
			recognition.onresult = (event) => {
				const transcript = Array.from(event.results)
					.map((result) => result[0].transcript)
					.join(" ")
					.trim();
				if (transcript) {
					const existing = this.doc.visit_notes ? this.doc.visit_notes + "\n" : "";
					this.set_field("visit_notes", existing + transcript);
					$card.find("textarea").val(this.doc.visit_notes);
				}
			};
			recognition.onend = () => (this._recognition = null);
			recognition.start();
		});
	}

	setup_signature($card) {
		const canvas = $card.find("canvas")[0];
		const resize = () => {
			const data = canvas.toDataURL();
			canvas.width = canvas.offsetWidth;
			canvas.height = canvas.offsetHeight;
			if (this._signature_drawn) {
				const image = new Image();
				image.onload = () => canvas.getContext("2d").drawImage(image, 0, 0);
				image.src = data;
			}
		};
		setTimeout(resize, 0);

		const context = canvas.getContext("2d");
		let drawing = false;
		const point = (event) => {
			const rect = canvas.getBoundingClientRect();
			return { x: event.clientX - rect.left, y: event.clientY - rect.top };
		};
		canvas.addEventListener("pointerdown", (event) => {
			if (this.doc.docstatus !== 0) return;
			drawing = true;
			canvas.setPointerCapture(event.pointerId);
			const { x, y } = point(event);
			context.strokeStyle = getComputedStyle(canvas).color || "#333";
			context.lineWidth = 2;
			context.lineCap = "round";
			context.beginPath();
			context.moveTo(x, y);
		});
		canvas.addEventListener("pointermove", (event) => {
			if (!drawing) return;
			const { x, y } = point(event);
			context.lineTo(x, y);
			context.stroke();
			this._signature_drawn = true;
		});
		canvas.addEventListener("pointerup", () => (drawing = false));
		$card.find(".vz-link-btn").on("click", () => {
			context.clearRect(0, 0, canvas.width, canvas.height);
			this._signature_drawn = false;
		});
		this._signature_canvas = canvas;

		if (this.doc.client_sign_off) {
			const image = new Image();
			image.onload = () => {
				context.drawImage(image, 0, 0, canvas.width || canvas.offsetWidth, canvas.height || canvas.offsetHeight);
			};
			image.src = this.doc.client_sign_off;
			this._signature_drawn = false; // existing signature isn't re-sent
		}
	}

	// ----- photos -----------------------------------------------------------

	bind_photo($btn, table, row) {
		$btn.on("click", () => {
			const input = document.createElement("input");
			input.type = "file";
			input.accept = "image/*";
			input.capture = "environment";
			input.onchange = () => {
				const file = input.files && input.files[0];
				if (!file) return;
				const form = new FormData();
				form.append("file", file, file.name);
				form.append("doctype", "Sapphire Maintenance Record");
				form.append("docname", this.doc.name);
				form.append("is_private", "1");
				fetch("/api/method/upload_file", {
					method: "POST",
					headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
					body: form,
				})
					.then((response) => response.json())
					.then((data) => {
						const file_url = data.message && data.message.file_url;
						if (!file_url) throw new Error("upload failed");
						this.set_row(table, row, "photo", file_url);
						$btn.addClass("vz-has-photo");
						frappe.show_alert({ message: __("Photo attached."), indicator: "green" });
					})
					.catch(() => {
						frappe.show_alert({ message: __("Photo upload failed."), indicator: "red" });
					});
			};
			input.click();
		});
	}

	// ----- finish -------------------------------------------------------------

	finish() {
		const signature =
			this._signature_drawn && this._signature_canvas
				? this._signature_canvas.toDataURL("image/png")
				: null;
		this.flush_save()
			.then(() =>
				frappe.call({
					method: "erpnext_enhancements.api.maintenance_visit.finish_visit",
					args: { record: this.doc.name, signature, modified: this.doc.modified },
					freeze: true,
					freeze_message: __("Finishing visit…"),
				})
			)
			.then((r) => {
				const state = (r && r.message) || {};
				const submitted = state.docstatus === 1;
				this.$wrap.empty().append(`
					<div class="vz-done-screen">
						<div class="vz-done-icon">${submitted ? "✅" : "📨"}</div>
						<h4>${submitted ? __("Visit submitted") : __("Sent for review")}</h4>
						<p class="vz-muted">${
							submitted
								? __("Stock, time and billing entries are being generated.")
								: __("A reviewer will approve and submit this visit.")
						}</p>
					</div>
				`);
				$(`<button type="button" class="vz-pick-card" style="text-align:center;font-weight:600;">${__("Back to Today's Visits")}</button>`)
					.on("click", () => {
						window.history.replaceState(null, "", "/app/visit-wizard");
						this.reset();
						this.show_picker();
					})
					.appendTo(this.$wrap.find(".vz-done-screen"));
			});
	}
}
