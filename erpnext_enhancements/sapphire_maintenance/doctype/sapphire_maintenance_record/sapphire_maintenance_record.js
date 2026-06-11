/**
 * Desk form script for the Sapphire Maintenance Record doctype.
 *
 * Loaded automatically by Frappe when the Maintenance Record form opens
 * (co-located with the doctype). Drives the technician's on-site workflow:
 *
 *  - setup:    restricts the consumables Item link to stock items in the
 *              configured consumables item group (ERPNext Enhancements
 *              Settings; empty = any stock item).
 *  - maintenance_contract / project / serial_no change: (a)
 *              populate_from_template instantiates all four section tables
 *              (results, chemistry readings, cleaning tasks, consumables)
 *              from the resolved modular template via the whitelisted
 *              `get_visit_payload` — asking before overwriting non-empty
 *              tables; (b) render_dashboard fetches `get_dashboard_context`
 *              and renders an in-form HTML briefing (safety instructions,
 *              access codes/site notes, contract access + cadence, service
 *              scope, recent visits). A scheduler-drafted record populates
 *              itself on first open (refresh with empty tables).
 *  - safety gate: all four section tables stay hidden behind an orange
 *              warning banner until the technician ticks
 *              `safety_acknowledged` (toggle_safety_gate).
 *
 * All server-supplied strings are passed through `frappe.utils.xss_sanitise`
 * before being injected into the dashboard HTML.
 */

const EE_SECTION_TABLES = ["maintenance_results", "chemistry_readings", "cleaning_tasks", "consumables"];

function ee_tables_empty(frm) {
	return EE_SECTION_TABLES.every((table) => !(frm.doc[table] || []).length);
}

function ee_tables_pristine(frm) {
	// True when the tech hasn't entered anything yet — rebuilding loses nothing.
	return (
		(frm.doc.maintenance_results || []).every((r) => !r.answer && !r.selection) &&
		(frm.doc.chemistry_readings || []).every((r) => !r.reading_value && !r.notes) &&
		(frm.doc.cleaning_tasks || []).every((r) => !r.is_done && !r.notes) &&
		(frm.doc.consumables || []).every((r) => !r.qty)
	);
}

function ee_apply_payload(frm, payload) {
	const mapping = {
		maintenance_results: payload.results,
		chemistry_readings: payload.readings,
		cleaning_tasks: payload.tasks,
		consumables: payload.consumables,
	};
	EE_SECTION_TABLES.forEach((table) => {
		frm.clear_table(table);
		(mapping[table] || []).forEach((row) => {
			Object.assign(frm.add_child(table), row);
		});
		frm.refresh_field(table);
	});
	if (payload.template) {
		frm.set_value("template", payload.template);
	}
}

// Web Speech API dictation into visit_notes — supported on Chrome/Android
// (the techs' field devices); the button simply doesn't appear elsewhere.
function ee_setup_dictation(frm) {
	const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
	if (!Recognition || frm.doc.docstatus !== 0) return;

	frm.add_custom_button(__("🎤 Dictate Note"), () => {
		if (frm._ee_recognition) {
			frm._ee_recognition.stop();
			return;
		}
		const recognition = new Recognition();
		frm._ee_recognition = recognition;
		recognition.lang = frappe.boot.lang || "en-US";
		recognition.interimResults = false;
		recognition.continuous = false;
		frappe.show_alert({ message: __("Listening… tap the button again to stop."), indicator: "blue" });

		recognition.onresult = (event) => {
			const transcript = Array.from(event.results)
				.map((r) => r[0].transcript)
				.join(" ")
				.trim();
			if (transcript) {
				const existing = frm.doc.visit_notes ? frm.doc.visit_notes + "\n" : "";
				frm.set_value("visit_notes", existing + transcript);
				frappe.show_alert({ message: __("Note added."), indicator: "green" });
			}
		};
		recognition.onerror = () => {
			frappe.show_alert({ message: __("Could not capture audio."), indicator: "red" });
		};
		recognition.onend = () => {
			frm._ee_recognition = null;
		};
		recognition.start();
	});
}

// Tiny inline SVG sparkline for the dashboard chemistry trends.
function ee_sparkline(points, min_value, max_value) {
	if (!points || points.length < 2) return "";
	const w = 110, h = 26, pad = 3;
	const values = points.map((p) => p.value);
	let low = Math.min.apply(null, values.concat(min_value || []));
	let high = Math.max.apply(null, values.concat(max_value || []));
	if (high === low) { high += 1; low -= 1; }
	const x = (i) => pad + (i * (w - 2 * pad)) / (points.length - 1);
	const y = (v) => h - pad - ((v - low) * (h - 2 * pad)) / (high - low);
	const path = points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + "," + y(p.value).toFixed(1)).join(" ");
	const dots = points
		.map((p, i) =>
			`<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.2" fill="${p.out_of_range ? "#dc2626" : "#2563eb"}"/>`
		)
		.join("");
	return (
		`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle;">` +
		`<path d="${path}" fill="none" stroke="#94a3b8" stroke-width="1.5"/>${dots}</svg>`
	);
}

frappe.ui.form.on("Sapphire Maintenance Record", {
	setup: function (frm) {
		// Filter items in consumables to stock items in the configured group
		frm.set_query("item", "consumables", function () {
			const filters = [["is_stock_item", "=", 1]];
			if (frm._ee_consumables_item_group) {
				filters.push(["item_group", "=", frm._ee_consumables_item_group]);
			}
			return { filters: filters };
		});
		frappe.db
			.get_single_value("ERPNext Enhancements Settings", "consumables_item_group")
			.then((group) => {
				frm._ee_consumables_item_group = group;
			});

		frm.set_query("serial_no", function () {
			return {
				filters: {
					// Add filters here if we want to restrict to specific items
				},
			};
		});

		frm.set_query("maintenance_contract", function () {
			const filters = { status: "Active" };
			if (frm.doc.project) {
				filters.project = frm.doc.project;
			}
			return { filters: filters };
		});
	},

	refresh: function (frm) {
		frm.trigger("toggle_safety_gate");
		frm.trigger("render_historical_visits");
		if (frm.doc.project) {
			frm.trigger("render_dashboard");
		}
		// Scheduler-drafted records arrive as bare headers: fill them on first open.
		if (frm.doc.docstatus === 0 && frm.doc.project && ee_tables_empty(frm)) {
			frm.trigger("populate_from_template");
		}

		// Visit completeness at a glance (computed server-side in validate).
		if (!frm.is_new() && !ee_tables_empty(frm)) {
			const pct = frm.doc.completion_percent || 0;
			const color = pct >= 100 ? "green" : pct >= 50 ? "orange" : "red";
			frm.dashboard.add_indicator(__("{0}% Complete", [pct]), color);
		}

		// The guided touch-first flow for techs — same record, no grids.
		if (!frm.is_new() && frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Open Visit Wizard"), () => {
				window.location.href = "/app/visit-wizard?record=" + encodeURIComponent(frm.doc.name);
			});
		}

		ee_setup_dictation(frm);
	},

	safety_acknowledged: function (frm) {
		frm.trigger("toggle_safety_gate");
	},

	toggle_safety_gate: function (frm) {
		const is_acknowledged = frm.doc.safety_acknowledged;
		EE_SECTION_TABLES.forEach((table) => {
			frm.set_df_property(table, "hidden", !is_acknowledged);
		});

		if (!is_acknowledged) {
			const overlay_html = `
				<div class="p-4 mb-4 text-orange-700 bg-orange-100 border-l-4 border-orange-500" role="alert">
					<p class="font-bold">Safety Compliance Required</p>
					<p>Please review safety instructions and check the <strong>Safety Procedures & PPE Acknowledged</strong> box to begin the checklist.</p>
				</div>
			`;
			frm.get_field("dashboard").$wrapper.prepend(overlay_html);
		} else {
			frm.get_field("dashboard").$wrapper.find(".bg-orange-100").remove();
		}
	},

	maintenance_contract: function (frm) {
		frm.trigger("populate_from_template");
		frm.trigger("render_dashboard");
	},

	project: function (frm) {
		frm.trigger("populate_from_template");
		frm.trigger("render_dashboard");
		frm.trigger("render_historical_visits");
	},

	serial_no: function (frm) {
		frm.trigger("populate_from_template");
		frm.trigger("render_dashboard");
	},

	populate_from_template: function (frm) {
		if (frm.doc.docstatus !== 0) return;
		if (!frm.doc.project && !frm.doc.maintenance_contract) return;

		const fill = () => {
			frappe.call({
				method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record.get_visit_payload",
				args: {
					project: frm.doc.project,
					serial_no: frm.doc.serial_no,
					maintenance_contract: frm.doc.maintenance_contract,
					technician: frm.doc.technician,
					visit_label: frm.doc.visit_label,
				},
				callback: function (r) {
					if (r.message) {
						ee_apply_payload(frm, r.message);
					}
				},
			});
		};

		if (ee_tables_empty(frm) || ee_tables_pristine(frm)) {
			fill();
		} else {
			frappe.confirm(
				__("Rebuild the visit form from the template? Entries already made in the tables below will be cleared."),
				fill
			);
		}
	},

	render_dashboard: function (frm) {
		if (!frm.doc.project) return;

		frappe.call({
			method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record.get_dashboard_context",
			args: {
				project: frm.doc.project,
				serial_no: frm.doc.serial_no,
			},
			callback: function (r) {
				if (r.message) {
					const ctx = r.message;
					const sanitise = frappe.utils.xss_sanitise;
					const safety = sanitise(ctx.profile.safety_instructions || "No specific safety instructions provided.");
					const codes = sanitise(ctx.profile.access_codes || ctx.contract.gate_code || "N/A");
					const site_instr = sanitise(ctx.serial_no.custom_site_instructions || "No specific site instructions.");

					let contract_html = "";
					if (ctx.contract && ctx.contract.name) {
						const parts = [];
						if (ctx.contract.key_location) {
							parts.push(`Key: <span class="font-mono">${sanitise(ctx.contract.key_location)}</span>`);
						}
						if (ctx.contract.preferred_days || ctx.contract.preferred_time) {
							parts.push(
								`Preferred: ${sanitise(ctx.contract.preferred_days || "")} ${sanitise(ctx.contract.preferred_time || "")}`
							);
						}
						if (parts.length) {
							contract_html = `<p class="mt-1 text-sm text-blue-700">${parts.join(" &middot; ")}</p>`;
						}
					}

					let trends_html = "";
					if (ctx.trends && ctx.trends.length) {
						const rows = ctx.trends
							.map((t) => {
								const latest = t.points[t.points.length - 1];
								return `
								<div class="flex justify-between items-center py-1 border-b border-gray-100 last:border-0">
									<span class="text-xs font-medium text-gray-600">${sanitise(t.reading)}</span>
									${ee_sparkline(t.points, t.min_value, t.max_value)}
									<span class="text-xs ${latest.out_of_range ? "font-bold text-red-600" : "text-gray-500"}">
										${latest.value} ${sanitise(t.uom || "")}
									</span>
								</div>`;
							})
							.join("");
						trends_html = `
							<div class="p-4 bg-indigo-50 border-l-4 border-indigo-400 rounded-r-md">
								<h3 class="text-sm font-bold text-indigo-800">Chemistry Trends (last visits)</h3>
								<div class="mt-2">${rows}</div>
							</div>
						`;
					}

					let scope_html = "";
					const deliverables = (ctx.service_scope && ctx.service_scope.deliverables) || [];
					if (deliverables.length) {
						scope_html = `
							<div class="p-4 bg-green-50 border-l-4 border-green-400 rounded-r-md">
								<h3 class="text-sm font-bold text-green-800">Contracted Service Scope</h3>
								<ul class="mt-2 text-sm text-green-700 list-disc list-inside">
									${deliverables.map((d) => `<li>${sanitise(d)}</li>`).join("")}
								</ul>
							</div>
						`;
					}

					let visits_html = "";
					if (ctx.visits && ctx.visits.length > 0) {
						visits_html = ctx.visits
							.map(
								(v) => `
							<div class="flex justify-between py-1 border-b border-gray-100 last:border-0">
								<span class="text-xs font-medium text-gray-600">${frappe.datetime.global_date_format(v.creation)}</span>
								<span class="text-xs text-gray-500">${sanitise(v.technician || "")}</span>
							</div>
						`
							)
							.join("");
					} else {
						visits_html = '<p class="text-xs text-gray-400">No recent visits found.</p>';
					}

					const dashboard_html = `
						<div class="space-y-4">
							<!-- Critical Safety -->
							<div class="p-4 border-l-4 bg-red-50 border-red-400 rounded-r-md">
								<div class="flex">
									<div class="flex-shrink-0">
										<svg class="w-5 h-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
											<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
										</svg>
									</div>
									<div class="ml-3">
										<h3 class="text-sm font-bold text-red-800">Safety Instructions</h3>
										<p class="mt-1 text-sm text-red-700">${safety}</p>
									</div>
								</div>
							</div>

							<!-- Site & Asset Context -->
							<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
								<div class="p-4 bg-blue-50 border-l-4 border-blue-400 rounded-r-md">
									<h3 class="flex items-center text-sm font-bold text-blue-800">
										<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
										Access & Site
									</h3>
									<p class="mt-2 text-sm text-blue-700">Code: <span class="font-mono font-bold">${codes}</span></p>
									${contract_html}
									<p class="mt-1 text-sm text-blue-700 italic">${site_instr}</p>
								</div>

								<!-- Historical Context -->
								<div class="p-4 bg-gray-50 border-l-4 border-gray-400 rounded-r-md">
									<h3 class="flex items-center text-sm font-bold text-gray-800">
										<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
										Recent Visits
									</h3>
									<div class="mt-2">
										${visits_html}
									</div>
								</div>
							</div>

							${trends_html}

							${scope_html}
						</div>
					`;
					frm.get_field("dashboard").$wrapper.html(dashboard_html);
					frm.trigger("toggle_safety_gate");
				}
			},
		});
	},

	// Read-only "Historical Visits" panel (HTML field) — last 5 submitted visits
	// for the Project, with a link back to each record. Replaces the former
	// virtual `historical_visits` child table. Depends only on the project, so
	// it's refreshed from `refresh` and the `project` change.
	render_historical_visits: function (frm) {
		const field = frm.get_field("historical_visits");
		if (!field) return;
		if (!frm.doc.project) {
			field.$wrapper.empty();
			return;
		}

		frappe.call({
			method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record.get_historical_visits",
			args: { project: frm.doc.project, exclude: frm.doc.name },
			callback: function (r) {
				const visits = r.message || [];
				const sanitise = frappe.utils.xss_sanitise;
				let rows_html;
				if (visits.length) {
					rows_html = visits
						.map(
							(v) => `
						<div class="flex justify-between items-center py-1 border-b border-gray-100 last:border-0">
							<a class="text-xs font-medium text-blue-600" href="/app/sapphire-maintenance-record/${encodeURIComponent(v.name)}">
								${frappe.datetime.global_date_format(v.creation)}
							</a>
							<span class="text-xs text-gray-500">${sanitise(v.technician || "")}</span>
						</div>`
						)
						.join("");
				} else {
					rows_html = '<p class="text-xs text-gray-400">No previous visits for this project.</p>';
				}

				const html = `
					<div class="p-4 bg-gray-50 border-l-4 border-gray-400 rounded-r-md">
						<h3 class="flex items-center text-sm font-bold text-gray-800">
							<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
							Historical Visits
						</h3>
						<div class="mt-2">${rows_html}</div>
					</div>
				`;
				field.$wrapper.html(html);
			},
		});
	},
});
