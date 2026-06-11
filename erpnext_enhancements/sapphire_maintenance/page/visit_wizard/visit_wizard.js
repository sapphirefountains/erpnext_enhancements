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
.vz-wrap{max-width:640px;margin:0 auto;padding-bottom:96px;}
.vz-muted{color:var(--text-muted);font-size:12px;}
.vz-progress{height:6px;border-radius:3px;background:var(--control-bg);margin:8px 0 4px;overflow:hidden;}
.vz-progress>div{height:100%;border-radius:3px;background:var(--primary,#2490ef);transition:width .25s;}
.vz-stepline{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--text-muted);margin-bottom:10px;}
.vz-tabs{display:flex;gap:8px;overflow-x:auto;padding:2px 0 10px;position:sticky;top:0;background:var(--bg-color);z-index:3;}
.vz-tab{flex:0 0 auto;padding:8px 14px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);font-size:13px;cursor:pointer;white-space:nowrap;}
.vz-tab.vz-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.vz-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;}
.vz-card.vz-bad{border-color:#dc2626;background:rgba(220,38,38,.06);}
.vz-card.vz-done{border-color:#15803d;}
.vz-card-title{font-weight:600;font-size:15px;}
.vz-card-sub{font-size:12px;color:var(--text-muted);margin-top:2px;}
.vz-chip{display:inline-block;font-size:11px;border-radius:10px;padding:1px 8px;margin-left:6px;vertical-align:middle;}
.vz-chip-red{background:#fde8e8;color:#b91c1c;}
.vz-chip-green{background:#e7f7ed;color:#15803d;}
.vz-num{width:100%;margin-top:10px;font-size:22px;text-align:center;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.vz-num:focus{outline:2px solid var(--primary,#2490ef);}
.vz-stepper{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;}
.vz-step-btn{flex:0 0 56px;height:48px;font-size:24px;line-height:1;border-radius:10px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);cursor:pointer;}
.vz-step-btn:active{background:var(--border-color);}
.vz-qty{flex:1;text-align:center;font-size:24px;font-weight:700;cursor:pointer;}
.vz-qty input{width:90px;font-size:22px;text-align:center;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg);color:var(--text-color);padding:6px;}
.vz-seg{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;}
.vz-seg button{flex:1 1 calc(25% - 8px);min-width:72px;min-height:44px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:13px;cursor:pointer;padding:6px 4px;}
.vz-seg button.vz-on{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.vz-seg button.vz-on.vz-neg{background:#dc2626;border-color:#dc2626;}
.vz-row-extra{margin-top:10px;display:flex;gap:8px;align-items:center;}
.vz-row-extra input{flex:1;padding:9px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;}
.vz-photo-btn{flex:0 0 auto;min-width:48px;min-height:42px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);cursor:pointer;font-size:17px;}
.vz-photo-btn.vz-has-photo{border-color:#15803d;background:#e7f7ed;}
.vz-check-card{display:flex;align-items:center;gap:12px;cursor:pointer;}
.vz-check-box{flex:0 0 28px;height:28px;border:2px solid var(--border-color);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;color:#fff;}
.vz-check-card.vz-on .vz-check-box{background:#15803d;border-color:#15803d;}
.vz-banner{border-radius:10px;padding:12px 14px;margin-bottom:10px;font-size:13px;}
.vz-banner-red{background:#fde8e8;color:#b91c1c;}
.vz-banner-blue{background:rgba(36,144,239,.1);color:var(--text-color);}
.vz-banner-green{background:#e7f7ed;color:#15803d;}
.vz-banner h6{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;}
.vz-nav{position:fixed;left:0;right:0;bottom:0;background:var(--card-bg);border-top:1px solid var(--border-color);padding:10px 16px calc(10px + env(safe-area-inset-bottom));z-index:5;}
.vz-nav-inner{max-width:640px;margin:0 auto;display:flex;gap:10px;}
.vz-nav button{flex:1;min-height:50px;border-radius:10px;font-size:16px;font-weight:600;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);cursor:pointer;}
.vz-nav button.vz-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;}
.vz-nav button:disabled{opacity:.45;cursor:not-allowed;}
.vz-textarea{width:100%;min-height:110px;margin-top:8px;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;}
.vz-sig{width:100%;height:160px;border:1px dashed var(--border-color);border-radius:10px;background:var(--card-bg);touch-action:none;}
.vz-link-btn{border:none;background:none;color:var(--primary,#2490ef);font-size:13px;cursor:pointer;padding:4px 0;}
.vz-pick-card{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;cursor:pointer;}
.vz-pick-card:active{border-color:var(--primary,#2490ef);}
.vz-empty{text-align:center;color:var(--text-muted);padding:40px 10px;}
.vz-done-screen{text-align:center;padding:48px 16px;}
.vz-done-screen .vz-done-icon{font-size:52px;}
.vz-readonly .vz-card,.vz-readonly .vz-nav{pointer-events:none;opacity:.75;}
`;

class VisitWizard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		$("<style>").text(VZ_STYLE).appendTo(page.body);
		this.$wrap = $('<div class="vz-wrap"></div>').appendTo(page.body);
		this.page.set_secondary_action(__("Reload"), () => this.reload(), "refresh");
		this.reset();
	}

	reset() {
		this.doc = null;
		this.dashboard = {};
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
		this.page.set_title(__("Today's Visits"));
		frappe.call("erpnext_enhancements.api.time_kiosk.get_my_visits_today").then((r) => {
			const visits = r.message || [];
			this.$wrap.empty();
			if (!visits.length) {
				this.$wrap.append(
					`<div class="vz-empty">${__("No open visits for today.")}<br>
					<a href="/app/sapphire-maintenance-record">${__("Open the record list")}</a></div>`
				);
				return;
			}
			visits.forEach((visit) => {
				const sub = visit.visit_label || visit.serial_no || __("Site visit");
				$(`<button class="vz-pick-card">
					<div class="vz-card-title">${frappe.utils.escape_html(visit.project_title || visit.project)}</div>
					<div class="vz-card-sub">${frappe.utils.escape_html(sub)} · ${frappe.utils.escape_html(visit.name)}</div>
				</button>`)
					.on("click", () => {
						window.history.replaceState(null, "", `/app/visit-wizard?record=${encodeURIComponent(visit.name)}`);
						this.load_record(visit.name);
					})
					.appendTo(this.$wrap);
			});
		});
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
		this.$wrap.append(`
			<div class="vz-stepline">
				<span>${__("Step {0} of {1}", [this.step_index + 1, this.steps.length])} · ${frappe.utils.escape_html(step.title)}</span>
				<span>${pct}%</span>
			</div>
			<div class="vz-progress"><div style="width:${pct}%"></div></div>
		`);

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
	}

	render_feature_tabs() {
		const $tabs = $('<div class="vz-tabs"></div>').appendTo(this.$wrap);
		this.features.forEach((serial) => {
			$(`<div class="vz-tab ${serial === this.feature ? "vz-active" : ""}">${frappe.utils.escape_html(serial)}</div>`)
				.on("click", () => {
					this.flush_save();
					this.feature = serial;
					this.render();
				})
				.appendTo($tabs);
		});
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

	// ----- step: safety ----------------------------------------------------

	render_safety() {
		const sanitise = frappe.utils.xss_sanitise;
		const profile = this.dashboard.profile || {};
		const serial = this.dashboard.serial_no || {};
		const contract = this.dashboard.contract || {};

		this.$wrap.append(`
			<div class="vz-banner vz-banner-red">
				<h6>${__("Safety Instructions")}</h6>
				${sanitise(profile.safety_instructions || __("No specific safety instructions provided."))}
			</div>
			<div class="vz-banner vz-banner-blue">
				<h6>${__("Access & Site")}</h6>
				${__("Code")}: <b>${sanitise(profile.access_codes || contract.gate_code || "N/A")}</b>
				${contract.key_location ? `<br>${__("Key")}: ${sanitise(contract.key_location)}` : ""}
				${serial.custom_site_instructions ? `<br>${sanitise(serial.custom_site_instructions)}` : ""}
			</div>
		`);

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
	}

	// ----- step: water chemistry --------------------------------------------

	render_readings() {
		this.rows("chemistry_readings").forEach((row) => {
			const range_label =
				row.min_value || row.max_value
					? `${row.min_value || 0} – ${row.max_value || "∞"} ${frappe.utils.escape_html(row.uom || "")}`
					: frappe.utils.escape_html(row.uom || "");
			const $card = $(`
				<div class="vz-card ${row.out_of_range ? "vz-bad" : ""}" data-reading-row="${frappe.utils.escape_html(row.name || "")}">
					<span class="vz-card-title">${frappe.utils.escape_html(row.reading || "")}</span>
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
			this.$wrap.append($card);
		});
	}

	// ----- step: chemicals used ----------------------------------------------

	render_consumables() {
		this.rows("consumables").forEach((row) => this.$wrap.append(this.consumable_card(row)));

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
		this.rows("maintenance_results").forEach((row) => {
			const options = (row.options || "").split("\n").map((option) => option.trim()).filter(Boolean);
			const choices = options.length ? options : ["Pass", "Fail", "Replace", "Other"];
			const $card = $(`
				<div class="vz-card ${row.selection || row.answer ? "vz-done" : ""}">
					<div class="vz-card-title">${frappe.utils.escape_html(row.question || "")}</div>
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
			this.$wrap.append($card);
		});
	}

	// ----- step: cleaning ---------------------------------------------------

	render_tasks() {
		this.rows("cleaning_tasks").forEach((row) => {
			const $card = $(`
				<div class="vz-card vz-check-card ${row.is_done ? "vz-on vz-done" : ""}">
					<div class="vz-check-box">${row.is_done ? "✓" : ""}</div>
					<div style="flex:1;">
						<div class="vz-card-title">${frappe.utils.escape_html(row.task || "")}</div>
						${row.notes ? `<div class="vz-card-sub">${frappe.utils.escape_html(row.notes)}</div>` : ""}
					</div>
				</div>
			`).on("click", () => {
				const done = row.is_done ? 0 : 1;
				this.set_row("cleaning_tasks", row, "is_done", done);
				$card.toggleClass("vz-on vz-done", !!done);
				$card.find(".vz-check-box").text(done ? "✓" : "");
			});
			this.$wrap.append($card);
		});
	}

	// ----- step: wrap-up ------------------------------------------------------

	render_wrapup() {
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
