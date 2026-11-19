// Build Checklist (v1.828.0, TASK-2026-03229 / -03230). One unit's build, sized for a phone or a
// tablet on the Build Room bench: the configurator's build steps as big tap rows, who is building it,
// and once it is built, its serial number, its label and its Pre-Shipment Functional Test.
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/build-checklist            Pick a Work Order (type or scan its name)
//   /desk/build-checklist/<WO>       That Work Order's checklist
//
// v16 fires on_page_show on every route change into this page, so moving from one Work Order to the
// next (or Back) arrives there and loads the one the route names.
//
// The rules are on the server (product_fulfillment/rules.py, checklist.py, qc_gate.py): what counts
// as done, who may tick, who may test. This file draws and wires. Every write returns the whole build
// again and the page redraws from it, so two phones on one unit never disagree for long.
// Styles: build_checklist.css, every selector under .pfb- (a Desk Page's stylesheet is global).

frappe.pages["build-checklist"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Build Checklist"),
		single_column: true,
	});
	wrapper.build_checklist = new BuildChecklistPage(page);
};

frappe.pages["build-checklist"].on_page_show = function (wrapper) {
	if (wrapper.build_checklist) wrapper.build_checklist.route_changed();
};

const PFB_PAGE = "build-checklist";
const PFB_API = "erpnext_enhancements.product_fulfillment.";

// The page's decisions, without the DOM.
const PFB = {
	work_order_from_route(route) {
		const name = (route || [])[1];
		return name ? decodeURIComponent(String(name)) : null;
	},

	// Steps in order, grouped by their section title, Build and QC apart.
	sections(steps, type) {
		const out = [];
		let current = null;
		(steps || [])
			.filter((s) => (s.step_type || "Build") === type)
			.forEach((step) => {
				const title = step.section_title || "";
				if (!current || current.title !== title) {
					current = { title, steps: [] };
					out.push(current);
				}
				current.steps.push(step);
			});
		return out;
	},

	unit_label(state) {
		return (
			{
				passed: __("Passed"),
				failed: __("Failed"),
				untested: __("Not tested"),
				tester_is_builder: __("Tested by its builder"),
				no_builder: __("No builder recorded"),
			}[state] || __("Not tested")
		);
	},

	unit_tone(state) {
		return state === "passed" ? "good" : state === "failed" ? "bad" : "watch";
	},

	same_person(a, b) {
		const x = String(a || "").trim().toLowerCase();
		return Boolean(x) && x === String(b || "").trim().toLowerCase();
	},
};

class BuildChecklistPage {
	constructor(page) {
		this.page = page;
		this.$body = $('<div class="pfb-page"></div>').appendTo(page.main);
		this.work_order = null;
		this.build = null;
		this.busy = false;
	}

	route_changed() {
		const name = PFB.work_order_from_route(frappe.get_route());
		this.work_order = name;
		this.build = null;
		if (!name) return this.draw_picker();
		this.page.set_title(name);
		this.load();
	}

	draw_picker() {
		this.page.set_title(__("Build Checklist"));
		this.$body.html(`
			<div class="pfb-card">
				<p class="pfb-lead">${__("Open a Work Order's checklist: type or scan its name.")}</p>
				<input class="pfb-input" type="text" autocomplete="off" placeholder="MFG-WO-2026-00001">
				<button class="btn btn-primary pfb-go">${__("Open")}</button>
			</div>`);
		const go = () => {
			const name = String(this.$body.find(".pfb-input").val() || "").trim();
			if (name) frappe.set_route(PFB_PAGE, name);
		};
		this.$body.find(".pfb-go").on("click", go);
		this.$body.find(".pfb-input").on("keydown", (e) => e.key === "Enter" && go()).trigger("focus");
	}

	// Reads go as GET (get_build takes GET only); writes as POST, Frappe's default.
	call(method, args, type) {
		return frappe
			.call({ method: PFB_API + method, args, freeze: false, type: type || "POST" })
			.then((r) => r && r.message);
	}

	load() {
		const asked = this.work_order;
		this.$body.html(`<div class="pfb-card pfb-muted">${__("Loading...")}</div>`);
		this.call("checklist.get_build", { work_order: asked }, "GET")
			.then((build) => {
				if (asked !== this.work_order) return; // the route moved on while this was loading
				this.build = build;
				this.draw();
			})
			.catch(() => {
				if (asked !== this.work_order) return;
				this.$body.html(
					`<div class="pfb-card pfb-bad">${__("Could not load Work Order {0}.", [frappe.utils.escape_html(asked)])}</div>`
				);
			});
	}

	draw() {
		const b = this.build;
		const esc = frappe.utils.escape_html;
		const progress = b.progress || { done: 0, total: 0 };
		const pct = progress.total ? Math.round((100 * progress.done) / progress.total) : 0;
		const builder = b.built_by
			? `<b>${esc(b.built_by_name || b.built_by)}</b>`
			: `<span class="pfb-muted">${__("Nobody yet")}</span>`;
		// Anyone may claim an unclaimed unit; only a System Manager may take it over from someone else (the
		// server refuses anyone else, since the builder is what keeps the builder from testing it).
		const can_claim =
			b.can_edit && !b.manufactured && !PFB.same_person(b.built_by, b.me) && (!b.built_by || b.is_manager);
		const header = `
			<div class="pfb-card pfb-head">
				<div class="pfb-part">${esc(b.production_item || "")}</div>
				<div class="pfb-muted">${esc(b.item_name || "")}</div>
				<div class="pfb-facts">
					<span><a href="/desk/work-order/${encodeURIComponent(b.name)}">${esc(b.name)}</a> &middot; ${esc(__(b.status || ""))}</span>
					${b.sales_order ? `<span>${esc(b.sales_order)}${b.customer_name ? " &middot; " + esc(b.customer_name) : ""}</span>` : ""}
					${b.expected_delivery_date ? `<span>${__("Due")} ${esc(frappe.datetime.str_to_user(b.expected_delivery_date))}</span>` : ""}
				</div>
				<div class="pfb-builder">
					<span>${__("Built by")}: ${builder}</span>
					${can_claim ? `<button class="btn btn-default pfb-claim">${__("I'm building this")}</button>` : ""}
				</div>
				<div class="pfb-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">
					<div class="pfb-bar-fill" style="width:${pct}%"></div>
				</div>
				<div class="pfb-muted">${__("{0} of {1} steps done", [progress.done, progress.total])}</div>
			</div>`;

		const build_sections = PFB.sections(b.steps, "Build")
			.map(
				(section) => `
				<div class="pfb-section">
					${section.title ? `<h4 class="pfb-section-title">${esc(section.title)}</h4>` : ""}
					${section.steps.map((step) => this.step_html(step, b.can_edit)).join("")}
				</div>`
			)
			.join("");
		const steps_html = (b.steps || []).length
			? build_sections
			: `<div class="pfb-card pfb-muted">${__("This Work Order has no build steps: it was not made from a Product Configuration.")}</div>`;

		const qc_sections = PFB.sections(b.steps, "QC");
		const tester_notes = qc_sections.length
			? `<div class="pfb-card">
				<h4 class="pfb-section-title">${__("For the tester (never the builder)")}</h4>
				${qc_sections
					.map(
						(section) => `
						${section.title ? `<div class="pfb-note-title">${esc(section.title)}</div>` : ""}
						<ul class="pfb-notes">${section.steps.map((s) => `<li>${esc(s.instruction)}</li>`).join("")}</ul>`
					)
					.join("")}
			</div>`
			: "";

		this.$body.html(header + steps_html + tester_notes + this.units_html(b));
		this.wire();
	}

	step_html(step, can_edit) {
		const esc = frappe.utils.escape_html;
		const who = step.done
			? `<div class="pfb-who">${esc(step.done_by_name || step.done_by || "")}${step.done_at ? " &middot; " + esc(frappe.datetime.str_to_user(step.done_at)) : ""}</div>`
			: "";
		return `
			<button type="button" class="pfb-step ${step.done ? "pfb-done" : ""}" data-step="${esc(step.name)}"
				data-done="${step.done ? 1 : 0}" ${can_edit ? "" : "disabled"} aria-pressed="${step.done ? "true" : "false"}">
				<span class="pfb-box" aria-hidden="true">${step.done ? "&#10003;" : ""}</span>
				<span class="pfb-text">${esc(step.instruction)}${who}</span>
			</button>`;
	}

	units_html(b) {
		const esc = frappe.utils.escape_html;
		if (!b.manufactured && !(b.units || []).length) {
			return `<div class="pfb-card pfb-muted">${__(
				"The serial number appears here once the unit is finished (Finish on the Work Order)."
			)}</div>`;
		}
		const rows = (b.units || [])
			.map((u) => {
				const label = `/printview?doctype=${encodeURIComponent("Serial No")}&name=${encodeURIComponent(
					u.serial
				)}&format=${encodeURIComponent("Product Unit Label")}&no_letterhead=1&trigger_print=1`;
				const builder_here = PFB.same_person(u.built_by, b.me);
				const test_button =
					b.can_test && !builder_here && u.state !== "passed"
						? `<button class="btn btn-primary btn-sm pfb-test" data-serial="${esc(u.serial)}">${
								(u.drafts || []).length ? __("Continue test") : __("Start test")
						  }</button>`
						: "";
				const open_test = u.test
					? `<a class="btn btn-default btn-sm" href="/desk/quality-inspection/${encodeURIComponent(u.test)}">${__("Open test")}</a>`
					: "";
				const why =
					builder_here && u.state !== "passed"
						? `<div class="pfb-muted">${__("You built this unit, so someone else tests it.")}</div>`
						: "";
				return `
					<div class="pfb-unit">
						<div class="pfb-serial">${esc(u.serial)}</div>
						<span class="pfb-pill pfb-${PFB.unit_tone(u.state)}">${esc(PFB.unit_label(u.state))}</span>
						${u.tested_by ? `<div class="pfb-muted">${__("Tested by")} ${esc(u.tested_by)}</div>` : ""}
						${why}
						<div class="pfb-actions">
							<a class="btn btn-default btn-sm" target="_blank" rel="noopener" href="${label}">${__("Print label")}</a>
							${open_test}${test_button}
						</div>
					</div>`;
			})
			.join("");
		return `<div class="pfb-card"><h4 class="pfb-section-title">${__("Units")}</h4>${rows}</div>`;
	}

	wire() {
		this.$body.find(".pfb-step").on("click", (e) => {
			const $step = $(e.currentTarget);
			if (this.busy || $step.prop("disabled")) return;
			const done = Number($step.attr("data-done")) ? 0 : 1;
			this.busy = true;
			$step.toggleClass("pfb-done", Boolean(done)).attr("aria-pressed", done ? "true" : "false");
			this.write("checklist.tick", { work_order: this.work_order, step: $step.attr("data-step"), done });
		});
		this.$body.find(".pfb-claim").on("click", () => {
			if (this.busy) return;
			this.busy = true;
			this.write("checklist.claim_build", { work_order: this.work_order });
		});
		this.$body.find(".pfb-test").on("click", (e) => {
			const serial = $(e.currentTarget).attr("data-serial");
			this.call("qc_gate.start_test", { serial_no: serial }).then((r) => {
				if (r && r.name) frappe.set_route("Form", "Quality Inspection", r.name);
			});
		});
	}

	write(method, args) {
		const asked = this.work_order;
		this.call(method, args)
			.then((build) => {
				if (asked !== this.work_order) return;
				this.build = build;
				this.draw();
			})
			.catch(() => {
				if (asked === this.work_order) this.load();
			})
			.finally(() => {
				this.busy = false;
			});
	}
}
