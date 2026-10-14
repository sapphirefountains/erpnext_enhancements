// Visit Wizard — the technician's guided, touch-first maintenance visit form.
//
// A desk Page (/desk/visit-wizard/MNT-REC-...) that steps through a
// Sapphire Maintenance Record one section at a time: Safety → Water
// Chemistry → Cleaning → Inspection → Chemicals Used → Wrap-up. Every input
// is a tap (steppers, segmented buttons, toggles) or a short numeric entry —
// no child-table grids. Without a record it lists today's open visits.
//
// Every screen is its own history entry, so the phone's Back button goes back
// one screen and Forward returns to it — see "routing" below for the
// addresses. ?record=MNT-REC-... (the kiosk's and the desk form's link) still
// opens a visit.
//
// It reads and writes the same Sapphire Maintenance Record as the desk form
// via api/maintenance_visit.py (bootstrap instantiates the template
// server-side; step changes autosave a field-allowlisted patch with
// optimistic locking; Finish applies the house workflow action). All
// downstream automation is untouched. The desk form remains the
// supervisor/review surface.
//
// What has not reached the server yet (answers, photos, a Finish) is kept on
// the phone and sent when there is signal (v1.596.0): see the note above
// vz_storage. The Safety step shows the site's last approved visit
// (render_last_visit).
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
	// Leaving for another Desk page (the sidebar, a link) sends what was typed,
	// as a step change does. The visit stays loaded, so Back comes back to it.
	$(wrapper).on("hide", () => wrapper.visit_wizard.save_on_hide());
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
.vz-savestate.vz-offline span{background:#fef3e2;color:#92400e;border-color:#d97706;font-weight:600;}
.vz-choice-list{margin:8px 0 0 18px;padding:0;}
.vz-choice-list li{margin-bottom:4px;}
.vz-choice-btn{display:block;width:100%;min-height:48px;margin-bottom:8px;border-radius:10px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:16px;font-weight:600;cursor:pointer;}
.vz-choice-btn.vz-choice-phone{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;}
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
.vz-photo-btn.vz-photo-waiting{border-color:#d97706;background:#fef3e2;}
.vz-fountain-photo{display:block;max-width:100%;max-height:220px;border-radius:8px;margin-top:8px;}
.vz-check-card{display:flex;align-items:center;gap:12px;cursor:pointer;}
.vz-check-box{flex:0 0 30px;height:30px;border:2px solid var(--border-color);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:19px;color:#fff;}
.vz-check-card.vz-on .vz-check-box{background:#15803d;border-color:#15803d;}
.vz-task-card .vz-check-card{align-items:flex-start;}
.vz-task-card.vz-na{opacity:.72;}
.vz-na-btn{flex:0 0 auto;min-width:56px;min-height:40px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;font-weight:600;cursor:pointer;}
.vz-na-btn.vz-on{background:#475569;border-color:#475569;color:#fff;}
.vz-why input:placeholder-shown{border-color:#d97706;}
.vz-why-task{font-size:14px;font-weight:600;margin-top:12px;}
.vz-follow-up{display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-top:1px solid var(--border-color);}
.vz-follow-up:first-of-type{border-top:none;}
.vz-follow-up-text{flex:1;font-size:14px;line-height:1.4;}
.vz-follow-up-text small{display:block;color:var(--text-muted);font-size:12.5px;}
.vz-add-btn{width:100%;margin-top:10px;min-height:46px;border-radius:10px;border:1px dashed var(--primary,#2490ef);background:none;color:var(--primary,#2490ef);font-size:15px;font-weight:600;cursor:pointer;}
.vz-follow-btn{flex:0 0 auto;min-height:44px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;padding:0 10px;}
.vz-banner{border-radius:10px;padding:12px 14px;margin-bottom:10px;font-size:15px;line-height:1.45;}
.vz-banner-red{background:#fde8e8;color:#b91c1c;}
.vz-banner-blue{background:rgba(36,144,239,.1);color:var(--text-color);}
.vz-banner-green{background:#e7f7ed;color:#15803d;}
.vz-brief{margin-top:10px;}
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
.vz-last-summary{font-size:13px;font-weight:400;color:var(--text-muted);text-align:right;}
.vz-last-feature{font-weight:600;margin:10px 0 2px;}
.vz-last-line{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px solid var(--border-color);}
.vz-last-bad{color:#b91c1c;font-weight:600;}
.vz-scope-row{padding:5px 0;border-bottom:1px solid var(--border-color);}
.vz-scope-row b{display:block;font-size:13px;font-weight:600;color:var(--text-muted);}
.vz-location{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--text-muted);margin:2px 0 8px;}
.vz-location a{color:var(--primary,#2490ef);font-weight:600;white-space:nowrap;}
.vz-group-head{font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;margin:16px 0 8px;}
.vz-nav{position:fixed;left:0;right:0;bottom:0;background:var(--card-bg);border-top:1px solid var(--border-color);padding:10px 16px calc(10px + env(safe-area-inset-bottom));z-index:5;}
.vz-nav-inner{max-width:640px;margin:0 auto;display:flex;gap:10px;}
.vz-nav button{flex:1;min-height:52px;border-radius:10px;font-size:17px;font-weight:600;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);cursor:pointer;}
.vz-nav button.vz-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;}
.vz-nav button:disabled{opacity:.45;cursor:not-allowed;}
.vz-textarea{width:100%;min-height:110px;margin-top:8px;padding:11px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.vz-date{width:100%;margin-top:8px;padding:11px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:17px;min-height:48px;}
.vz-sig{width:100%;height:160px;border:1px dashed var(--border-color);border-radius:10px;background:var(--card-bg);touch-action:none;}
.vz-tech-sig.vz-sig-missing .vz-sig{border:2px solid #d97706;}
.vz-sig-needed{color:#b45309;font-size:14px;font-weight:600;margin-top:6px;}
.vz-sig-img{display:block;max-width:100%;max-height:120px;margin-top:8px;background:#fff;border-radius:8px;}
.vz-sign-off-email-toggle{display:flex;align-items:center;gap:10px;margin-top:12px;font-size:15px;cursor:pointer;}
.vz-sign-off-email-toggle input{width:22px;height:22px;flex:0 0 22px;}
.vz-email{width:100%;margin-top:8px;padding:11px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:17px;min-height:48px;}
.vz-sign-off-sent{margin-top:8px;}
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
// How far back a visit may be dated: api/maintenance_visit.py MAX_BACKFILL_DAYS, which the
// server enforces (checked_visit_date). Mirrored only to bound the date picker.
const VZ_MAX_BACKFILL_DAYS = 60;

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
	cleaning_tasks: (row) => vz_task_state(row) !== "open" || !!String(row.notes || "").trim(),
	consumables: () => true,
};

// Fields that together make one answer, judged together when kept answers meet the server.
const VZ_PAIRED_FIELDS = { cleaning_tasks: ["is_done", "not_applicable"] };

// A cleaning task is done, N/A or not done (v1.598.0); done wins, as the record's validate
// settles a row ticked both ways. MIRRORS sapphire_maintenance/task_rules.py.
function vz_task_state(row) {
	if (row.is_done) return "done";
	if (row.not_applicable) return "na";
	return "open";
}

// Left not done with nothing said about why: what the wrap-up asks about.
function vz_task_unexplained(row) {
	return vz_task_state(row) === "open" && !String(row.notes || "").trim();
}

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

// The Whole site tab's key (v1.634.0): rows of a form ticked Once per Site Visit carry no
// serial_no. Not "" because an empty feature reads as "no tab" in the route and in go().
const VZ_SITE = "whole-site";

function vz_plain_html(value) {
	return frappe.utils.escape_html(String(value == null ? "" : value)).replace(/\n/g, "<br>");
}

// Rows added on this phone have no name until the server has them: consumables (v1.596.0)
// and follow-ups (v1.600.0). Each carries its table as `_table`; a row kept on a phone before
// v1.600.0 has none and is a consumable. Per table: the fields sent, the fields kept on the
// phone, and whether a row on the server is this one, sent before but its answer lost.
const VZ_ADDED = {
	consumables: {
		sent: ["item", "qty", "warehouse", "uom"],
		kept: ["item", "item_name", "qty", "uom", "warehouse", "qty_step", "serial_no"],
		// The server does not get a feature for a consumable added here.
		same: (server, row) =>
			server.item === row.item &&
			vz_same(server.qty, row.qty) &&
			vz_same(server.uom, row.uom) &&
			(!server.serial_no || server.serial_no === row.serial_no),
	},
	follow_ups: {
		sent: ["description", "item", "qty", "told", "serial_no", "source_question"],
		kept: ["description", "item", "qty", "told", "serial_no", "source_question"],
		// The server stores a blank qty as 0, and may rewrite a description's markup and
		// spacing when it sanitizes Small Text, so both are compared as the server keeps them.
		same: (server, row) =>
			vz_loose_text(server.description) === vz_loose_text(row.description) &&
			vz_same(server.item, row.item) &&
			vz_same(server.qty || 0, row.qty || 0) &&
			vz_same(server.serial_no, row.serial_no),
	},
};

// Text as it survives the server's sanitizing: no tags or entities, spacing collapsed.
function vz_loose_text(value) {
	return String(value == null ? "" : value)
		.replace(/<[^>]*>/g, "")
		.replace(/&[#\w]+;/g, "")
		.replace(/[<>&]/g, "")
		.replace(/\s+/g, " ")
		.trim();
}

function vz_added_table(row) {
	return row._table || "consumables";
}

// The save_visit patch for a batch of edits: changed parent fields, changed rows
// by name, and rows added here (no name yet — the server appends them).
function vz_patch(dirty, pending_added) {
	const rows = {};
	Object.entries(dirty.rows).forEach(([table, by_name]) => {
		rows[table] = Object.entries(by_name).map(([name, changes]) => ({ name, ...changes }));
	});
	pending_added.forEach((row) => {
		const table = vz_added_table(row);
		const sent = {};
		VZ_ADDED[table].sent.forEach((field) => (sent[field] = row[field]));
		(rows[table] = rows[table] || []).push(sent);
	});
	return { fields: dirty.fields, rows };
}

// A save the server answered and turned down — out of date (someone saved the
// visit since, or an earlier save committed but its answer never arrived), not
// permitted, gone — is turned down again on every retry, so it is not retried.
// No answer at all (status 0: a bad signal) or a server error may pass next
// time, and is. frappe.call rejects with the jqXHR, which carries the status.
function vz_refused(error) {
	const status = (error && error.status) || 0;
	return status >= 400 && status < 500 && ![401, 408, 429].includes(status);
}

// What the server said when it turned a call down (frappe.throw's message), as plain text, or
// "". A call made in the background is `silent`, so frappe shows nothing: a Finish refused
// there is said with these words (v1.610.0: a Finish kept on a phone before the technician's
// signature was required is refused with "The technician must sign the visit before it goes
// for review."). Escape it before it goes into markup.
function vz_server_message(error) {
	let messages = [];
	try {
		messages = JSON.parse(((error || {}).responseJSON || {})._server_messages || "[]");
	} catch (e) {
		return "";
	}
	return (Array.isArray(messages) ? messages : [])
		.map((message) => {
			try {
				const parsed = typeof message === "string" ? JSON.parse(message) : message;
				return String((parsed && parsed.message) || "");
			} catch (e) {
				return String(message || "");
			}
		})
		.filter(Boolean)
		.join(" ");
}

// Two batches of unsaved edits as one; `top` wins wherever both touched a field.
function vz_merge_dirty(base, top) {
	const merged = { fields: { ...base.fields, ...top.fields }, rows: {} };
	[base.rows, top.rows].forEach((rows) => {
		Object.entries(rows).forEach(([table, by_name]) => {
			const into = (merged.rows[table] = merged.rows[table] || {});
			Object.entries(by_name).forEach(([name, changes]) => {
				into[name] = { ...(into[name] || {}), ...changes };
			});
		});
	});
	return merged;
}

// ----- kept on this phone until the server has it (v1.596.0, TASK-2026-02483) -----
//
// Until v1.596.0 every answer not yet saved lived only in this page's memory. Saves retried
// on a backoff and parked against their own visit, but a reload, a crash, or the phone
// closing the browser on a site with no signal dropped them. Nik's decision of 2026-10-12:
// "never lose answers", without a full offline mode. The visit is still opened with signal
// (at the truck), and from then on nothing the technician enters is lost:
//
//   * Answers: what has not reached the server for a visit is written to localStorage
//     (`vz-unsent:<visit>`) on every change: the changed fields and rows, consumables added
//     here, edits parked against a visit left behind, and a save still on the wire. It is
//     the union of all of them (VisitWizard.unsent), rewritten when a save lands, and
//     removed when nothing is left.
//   * Photos, and a Finish (with the drawn signature), go into IndexedDB (`vz-outbox`):
//     too big for localStorage, and a Finish has to survive the tab. They are sent when
//     signal returns: the page's "online" event, a save that gets through, the page
//     opening again, and a backoff while anything is waiting.
//   * Opening a visit puts back what this phone kept for it. Each kept answer remembers the
//     server's value from before it was changed (`base`), so the server's current version
//     decides (vz_reconcile): unchanged on the server, the phone's answer goes back on;
//     already there (a save that landed and lost its answer), it is dropped; changed by
//     someone else, the technician is asked. A newer server edit is never overwritten
//     without saying so.
//
// Nothing here can stop the page working: storage that is blocked or full (a private
// window, site data turned off) leaves the wizard as it was, and its save state says
// "NOT SAVED" rather than "Saved on this phone".
const VZ_UNSENT_KEY = "vz-unsent:";
const VZ_UNSENT_INDEX = "vz-unsent-index";
const VZ_OUTBOX_DB = "vz-outbox";
const VZ_OUTBOX_STORE = "items";

function vz_storage() {
	try {
		return window.localStorage || null;
	} catch (e) {
		return null; // blocked: nothing is kept, as before v1.596.0
	}
}

function vz_unsent_names() {
	const store = vz_storage();
	if (!store) return [];
	try {
		return JSON.parse(store.getItem(VZ_UNSENT_INDEX) || "[]");
	} catch (e) {
		return [];
	}
}

function vz_unsent_read(name) {
	const store = vz_storage();
	if (!store || !name) return null;
	try {
		return JSON.parse(store.getItem(VZ_UNSENT_KEY + name) || "null");
	} catch (e) {
		return null;
	}
}

// Keep `entry` for visit `name`, or forget it (null). True when the phone now holds it.
function vz_unsent_write(name, entry) {
	const store = vz_storage();
	if (!store || !name) return false;
	try {
		const names = vz_unsent_names().filter((other) => other !== name);
		if (entry) {
			store.setItem(VZ_UNSENT_KEY + name, JSON.stringify(entry));
			names.push(name);
		} else {
			store.removeItem(VZ_UNSENT_KEY + name);
		}
		store.setItem(VZ_UNSENT_INDEX, JSON.stringify(names));
		return !!entry;
	} catch (e) {
		return false; // full or blocked
	}
}

// A {fields, rows} batch with nothing in it.
function vz_no_changes(dirty) {
	return !Object.keys((dirty || {}).fields || {}).length && !Object.keys((dirty || {}).rows || {}).length;
}

function vz_unsent_empty(entry) {
	return !entry || (vz_no_changes(entry.dirty) && !(entry.pending_added || []).length && vz_no_changes(entry.held));
}

// Worth sending again later, rather than said and stopped. No answer at all (a bad signal,
// jqXHR status 0, a fetch that never reached the server), or an answer that is not the
// server's own verdict on the visit: an expired session (401), a timeout or throttling (408,
// 429), a gateway while the bench restarts, which every deploy does (502, 503, 504), or a
// server error with no Frappe exception in its body. Only Frappe turning the request down
// (an exception it raised, a 4xx) is final.
const VZ_TRY_LATER = [401, 408, 429, 502, 503, 504];
function vz_try_later(error) {
	if (!error || !error.status) return true;
	if (VZ_TRY_LATER.includes(error.status)) return true;
	return error.status >= 500 && !(error.responseJSON && error.responseJSON.exc_type);
}

// The consumables added here, as plain data the patch needs (and nothing that is not JSON).
function vz_plain_added(rows) {
	return rows.map((row) => {
		const table = vz_added_table(row);
		const plain = { _table: table };
		VZ_ADDED[table].kept.forEach((field) => (plain[field] = row[field]));
		return plain;
	});
}

// The server's values from before each unsent change, for exactly the changes still unsent.
function vz_prune_base(base, dirty) {
	const kept = { fields: {}, rows: {} };
	if (!base) return kept;
	Object.keys(dirty.fields).forEach((field) => {
		if (field in (base.fields || {})) kept.fields[field] = base.fields[field];
	});
	Object.entries(dirty.rows).forEach(([table, by_name]) => {
		Object.entries(by_name).forEach(([name, changes]) => {
			const old = ((base.rows || {})[table] || {})[name] || {};
			Object.keys(changes).forEach((field) => {
				if (field in old) {
					((kept.rows[table] = kept.rows[table] || {})[name] = kept.rows[table][name] || {})[field] =
						old[field];
				}
			});
		});
	});
	return kept;
}

// One answer, however it is typed: blank, null and "" are the same; 7.4 and "7.4" are too.
function vz_same(a, b) {
	const blank = (v) => v === null || v === undefined || v === "";
	if (blank(a) || blank(b)) return blank(a) && blank(b);
	const x = Number(a);
	const y = Number(b);
	if (Number.isFinite(x) && Number.isFinite(y)) return x === y;
	return String(a) === String(b);
}

// What to do with the answers this phone kept for a visit, against the server's version of
// it now (`doc`). Returns {apply, added, conflicts, dropped}: `apply` and `conflicts` are
// [{table, row, field, value, server}] (table null for a field on the visit itself), `added`
// the consumables still to add, `dropped` how many the server already has. `entry.held` are
// answers the technician was already asked about and has not chosen on: they are asked
// about again, whatever the version, unless the server now has the phone's value.
function vz_reconcile(entry, doc) {
	const out = { apply: [], added: [], conflicts: [], dropped: 0 };
	const same_version = !!entry.modified && entry.modified === doc.modified;
	const base = entry.base || { fields: {}, rows: {} };
	const judge = (table, row, field, value, server, before, has_before) => {
		const change = { table, row, field, value, server };
		if (same_version) out.apply.push(change);
		else if (vz_same(server, value)) out.dropped += 1;
		else if (has_before && vz_same(server, before)) out.apply.push(change);
		else out.conflicts.push(change);
	};
	Object.entries((entry.dirty || {}).fields || {}).forEach(([field, value]) => {
		const has = field in (base.fields || {});
		judge(null, null, field, value, doc[field], has ? base.fields[field] : undefined, has);
	});
	Object.entries((entry.dirty || {}).rows || {}).forEach(([table, by_name]) => {
		Object.entries(by_name).forEach(([name, changes]) => {
			const row = (doc[table] || []).find((candidate) => candidate.name === name);
			if (!row) return; // gone from the visit since: nowhere to put it
			const old = ((base.rows || {})[table] || {})[name] || {};
			Object.entries(changes).forEach(([field, value]) => {
				judge(table, name, field, value, row[field], old[field], field in old);
			});
		});
	});
	const held = entry.held || { fields: {}, rows: {} };
	const ask = (table, name, field, value, server) => {
		if (vz_same(server, value)) out.dropped += 1;
		else out.conflicts.push({ table, row: name, field, value, server });
	};
	Object.entries(held.fields || {}).forEach(([field, value]) => ask(null, null, field, value, doc[field]));
	Object.entries(held.rows || {}).forEach(([table, by_name]) => {
		Object.entries(by_name).forEach(([name, changes]) => {
			const row = (doc[table] || []).find((candidate) => candidate.name === name);
			if (!row) return;
			Object.entries(changes).forEach(([field, value]) => ask(table, name, field, value, row[field]));
		});
	});
	// A cleaning task's Done and N/A are one answer (v1.598.0). When either is in question, so
	// is the other: applying the half nobody disputes would make a task both done and N/A, which
	// the server settles as done without asking, over the office's N/A.
	out.conflicts.slice().forEach((conflict) => {
		const pair = VZ_PAIRED_FIELDS[conflict.table];
		if (!pair || !pair.includes(conflict.field)) return;
		out.apply = out.apply.filter((change) => {
			const partner = change.table === conflict.table && change.row === conflict.row && pair.includes(change.field);
			if (partner) out.conflicts.push(change);
			return !partner;
		});
	});
	// A row added here (a consumable, a follow-up) has no name until the server has it. On
	// another version of the visit, a matching row may be one this phone sent whose answer was
	// lost: one the phone did not know of when it kept the answers (`known`, by table; a plain
	// list, kept before v1.600.0, is the consumables), and the same by VZ_ADDED's rule.
	const known_in = (table) => {
		const known = entry.known || {};
		if (Array.isArray(known)) return table === "consumables" ? known : [];
		return known[table] || [];
	};
	const unmatched = {};
	(entry.pending_added || []).forEach((row) => {
		const table = vz_added_table(row);
		if (!same_version) {
			if (!unmatched[table]) {
				const known = new Set(known_in(table));
				unmatched[table] = (doc[table] || []).filter((server_row) => !known.has(server_row.name));
			}
			const index = unmatched[table].findIndex((server_row) => VZ_ADDED[table].same(server_row, row));
			if (index >= 0) {
				unmatched[table].splice(index, 1);
				out.dropped += 1;
				return;
			}
		}
		out.added.push(row);
	});
	return out;
}

// Two kept entries for one visit as one (v1.610.0: answers kept under a visit's old name and
// under its new one): `newer`'s answers win where both changed a field, and each field keeps the
// earliest server value from before it was changed (`base`), which is what vz_reconcile compares
// with. The older entry's version stands, so a server that has moved on since is reconciled.
function vz_merge_kept(older, newer) {
	const empty = () => ({ fields: {}, rows: {} });
	const held = older.held || newer.held ? vz_merge_dirty(older.held || empty(), newer.held || empty()) : null;
	return {
		...newer,
		record: newer.record,
		modified: older.modified || newer.modified || null,
		dirty: vz_merge_dirty(older.dirty || empty(), newer.dirty || empty()),
		pending_added: (older.pending_added || []).concat(newer.pending_added || []),
		held,
		base: vz_merge_dirty(newer.base || empty(), older.base || empty()),
		known: newer.known || older.known || {},
	};
}

// One change ({table, row, field, value}; table null for a field on the visit itself) into
// a {fields, rows} batch of edits.
function vz_put_change(dirty, change) {
	if (!change.table) {
		dirty.fields[change.field] = change.value;
		return dirty;
	}
	const rows = (dirty.rows[change.table] = dirty.rows[change.table] || {});
	(rows[change.row] = rows[change.row] || {})[change.field] = change.value;
	return dirty;
}

// A change made away from the screen (an uploaded photo's url), into the answers this phone
// keeps for visit `name`. Its server value from before is taken as blank, a photo not yet
// attached, so opening the visit puts it on unless somebody attached another meanwhile.
function vz_keep_change(entry, name, change, modified) {
	entry = entry || {
		record: name,
		modified: modified || null,
		dirty: { fields: {}, rows: {} },
		pending_added: [],
		base: { fields: {}, rows: {} },
	};
	vz_put_change(entry.dirty, change);
	entry.base = entry.base || { fields: {}, rows: {} };
	entry.base.fields = entry.base.fields || {};
	entry.base.rows = entry.base.rows || {};
	if (!change.table) {
		if (!(change.field in entry.base.fields)) entry.base.fields[change.field] = null;
	} else {
		const rows = (entry.base.rows[change.table] = entry.base.rows[change.table] || {});
		const old = (rows[change.row] = rows[change.row] || {});
		if (!(change.field in old)) old[change.field] = null;
	}
	entry.at = Date.now();
	vz_unsent_write(name, entry);
	return entry;
}

// IndexedDB: the photos and Finishes waiting for signal. Resolves null when the browser has
// none (or refuses it), and the page then works as it did before v1.596.0.
function vz_outbox_db() {
	if (!vz_outbox_db.opening) {
		vz_outbox_db.opening = new Promise((resolve) => {
			let idb = null;
			try {
				idb = window.indexedDB || null;
			} catch (e) {
				idb = null;
			}
			if (!idb) return resolve(null);
			let request;
			try {
				request = idb.open(VZ_OUTBOX_DB, 1);
			} catch (e) {
				return resolve(null);
			}
			request.onupgradeneeded = () => request.result.createObjectStore(VZ_OUTBOX_STORE, { keyPath: "key" });
			request.onsuccess = () => resolve(request.result);
			request.onerror = () => resolve(null);
			// A browser that never answers (some private modes) must not hold the page up: the
			// visit list and loading a visit wait on this.
			setTimeout(() => resolve(null), 3000);
		});
	}
	return vz_outbox_db.opening;
}

// One request in its own transaction, settled when the transaction completes (a write is
// on disk then, not merely queued).
function vz_outbox(mode, work) {
	return vz_outbox_db().then((db) => {
		if (!db) return null;
		return new Promise((resolve, reject) => {
			try {
				const tx = db.transaction(VZ_OUTBOX_STORE, mode);
				const request = work(tx.objectStore(VZ_OUTBOX_STORE));
				tx.oncomplete = () => resolve(request.result === undefined ? true : request.result);
				tx.onerror = tx.onabort = () => reject(tx.error);
			} catch (e) {
				reject(e);
			}
		});
	});
}

const vz_outbox_put = (item) => vz_outbox("readwrite", (store) => store.put(item));
const vz_outbox_delete = (key) => vz_outbox("readwrite", (store) => store.delete(key));
const vz_outbox_items = () =>
	vz_outbox("readonly", (store) => store.getAll()).then(
		(items) => (Array.isArray(items) ? items : []),
		() => []
	);
// The answer a kept photo belongs to: a row's `photo`, or (v1.597.0) a field on the visit
// itself, such as the fountain as the technician left it (table and row empty).
function vz_photo_change(item, url) {
	return { table: item.table || null, row: item.row || null, field: item.field || "photo", value: url };
}

// One key per photo taken, this prefix plus the time it was taken (take_photo): a retake while
// the first is still uploading must not share its key, or deleting the uploaded first would
// delete the retake with it.
const vz_photo_prefix = (record, table, row) => `photo|${record}|${table}|${row}|`;
const vz_finish_key = (record) => `finish|${record}`;

// upload_file, as the page has always sent a photo: the file url, or a rejection. A
// rejection with a `status` is the server turning it down (vz_refused tells which kind);
// one without is no signal. `fieldname` is for a photo on the visit itself (the fountain
// photo, v1.597.0): frappe's on_update hook looks for a File attached to that field, and
// inserts a second File row for the same photo when the upload did not name it.
function vz_upload(blob, filename, record, fieldname) {
	const form = new FormData();
	form.append("file", blob, filename || "photo.jpg");
	form.append("doctype", "Sapphire Maintenance Record");
	form.append("docname", record);
	if (fieldname) form.append("fieldname", fieldname);
	form.append("is_private", "1");
	return fetch("/api/method/upload_file", {
		method: "POST",
		headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		body: form,
	})
		.then((response) => {
			if (response.ok) return response.json();
			// Frappe's own errors come as JSON with an exc_type; a gateway's are a page.
			return Promise.resolve(response.json ? response.json() : null)
				.catch(() => null)
				.then((body) => {
					const error = new Error("upload refused");
					error.status = response.status;
					error.responseJSON = body;
					throw error;
				});
		})
		.then((data) => {
			const file_url = data && data.message && data.message.file_url;
			if (!file_url) throw new Error("upload failed");
			return file_url;
		});
}

class VisitWizard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		$("<style>").text(VZ_STYLE).appendTo(page.body);
		this.$wrap = $('<div class="vz-wrap"></div>').appendTo(page.body);
		this.page.set_secondary_action(__("Reload"), () => this.reload(), "refresh");
		// Edits that outlived the screen they were made on, by record name (see
		// park()), and the save of each record still on the wire. Neither is part
		// of reset(): both belong to a record, not to what is on screen.
		this._parked = {};
		this._park_warned = new Set();
		this._saving = {};
		// Kept on this phone (v1.596.0; see the note above vz_storage). Like the parked
		// saves, all of it belongs to a record, not to the screen, so none is in reset():
		// the batch of each visit's save on the wire (unsent until it is answered), the
		// server's value from before each unsent change, visits whose Finish waits on this
		// phone (their copy here is the visit's until it is sent), photos waiting, and
		// visits whose kept answers clash with the server's (opening one asks).
		this._inflight = {};
		this._base = {};
		this._handed = new Set();
		this._photo_waiting = new Set();
		this._photo_runs = {};
		this._needs_you = new Set();
		this._held = {};
		this._finish = {};
		this._photo_again = new Set();
		this._journal_failed = false;
		this._sync = null;
		this._sync_attempt = 0;
		// Bumped whenever the picker or a visit is asked for, so a slow answer for
		// a screen the technician has already left is dropped instead of painted.
		this._paint_seq = 0;
		// Closing the tab mid-visit used to lose whatever had not autosaved yet,
		// silently. Technicians work on phones and switch apps constantly. Since
		// v1.596.0 the answers are kept on the phone, so this warns only where the
		// browser will not keep them.
		window.addEventListener("beforeunload", (event) => {
			if (
				(!vz_storage() || this._journal_failed) &&
				((this.doc && this.doc.docstatus === 0 && this.has_dirty()) || Object.keys(this._parked).length)
			) {
				event.preventDefault();
				event.returnValue = "";
				return "";
			}
		});
		// Signal is back: send what waited for it.
		window.addEventListener("online", () => this.signal_back());
		// What this phone was already holding when the page opened: a Finish makes its
		// visit wait for signal, a photo marks its camera button. Loading a visit waits
		// for this, then everything kept is sent.
		this._outbox_ready = vz_outbox_items().then((items) => {
			items.forEach((item) => {
				if (item.kind === "finish") {
					this._finish[item.record] = item;
					// Turned down, or held back for a choice: the visit opens instead.
					if (!item.refused && !item.blocked) this._handed.add(item.record);
				}
				if (item.kind === "photo" && !item.refused) this._photo_waiting.add(item.key);
			});
		});
		this._outbox_ready.then(() => this.sync_stored());
		this.reset();
	}

	reset() {
		this.doc = null;
		this.dashboard = {};
		this.section_meta = {};
		this.template_meta = {};
		this.feature_names = {};
		// v1.614.0: the client sign-off by email — the address to prefill and the link already
		// out (from the bootstrap), and the technician's choice on this visit (render_sign_off_email).
		this.sign_off = {};
		this._email_sign_off = { on: false, email: null };
		this.steps = [];
		this.step_index = 0;
		this.features = [];
		this.feature = null;
		this.dirty = { fields: {}, rows: {} };
		this.pending_added = [];
		this.safety_ok = false;
		// Cleared, not just forgotten: a timer left running fires into the next
		// visit, and a stale "NOT SAVED" or signature must not follow the
		// technician onto a different record.
		clearTimeout(this._save_timer);
		clearTimeout(this._retry_timer);
		clearTimeout(this._saved_timer);
		this._save_timer = null;
		this._retry_attempt = 0;
		this._save_state = null;
		this._loading = null;
		this._load_target = null;
		// The Wrap-up's two signature pads, by the field each fills (setup_signature): the
		// client's sign-off and, since v1.610.0, the technician's own.
		this._pads = {};
		// Kept answers waiting on the technician's choice (render_choice) for the visit on
		// screen; the answers themselves are held per visit in this._held.
		this._choice = null;
	}

	// ----- routing -------------------------------------------------------
	//
	// One history entry per screen, so the phone's Back button goes back one
	// screen and Forward comes back to it:
	//
	//   /desk/visit-wizard                           the picker
	//   /desk/visit-wizard/<record>                  a visit, on its first step
	//   /desk/visit-wizard/<record>/<step>           a step: safety, readings, ...
	//   /desk/visit-wizard/<record>/<step>/<serial>  a feature tab on that step
	//
	// ?record=<name> (the kiosk's and the desk form's link) opens a visit too.
	//
	// The page moves with frappe.set_route and frappe owns history: Back and
	// Forward re-render the route, which fires on_page_show -> handle_route, and
	// there is no popstate listener here. The page used to write its own address
	// with history.replaceState — never a new entry, so Back left the wizard
	// from any step — and wrote it as /app/visit-wizard, which the v16 router
	// cannot parse: a Back or Forward onto one of those entries showed "Page
	// not found". A hand-built path is how that happened, so there is none here.

	handle_route() {
		const target = this.route_target();
		if (!target) return;
		this._url_target = target;
		if (!target.record) {
			this.leave_record();
			this.reset();
			this.show_picker();
			return;
		}
		if (this.doc && this.doc.name === target.record) {
			this.go(this.step_for(target.step), { feature: target.feature, from_route: true });
			return;
		}
		if (this._loading === target.record) {
			// Back/Forward again while this visit is still loading: land on the
			// newest step asked for once it arrives.
			this._load_target = target;
			return;
		}
		this.leave_record();
		this.load_record(target.record, target);
	}

	// What the address asks for: {record, step, feature}. Path segments win;
	// ?record= and frappe.set_route("visit-wizard", {record}) arrive in
	// frappe.route_options, which is consumed like the list view does — the
	// router merges every address's query into it and never clears it, so an
	// unconsumed key would reopen this visit on the next plain /desk/visit-wizard.
	//
	// Only ?record= in the address itself (the kiosk's link, a full load) names
	// the visit in that history entry. frappe.set_route("visit-wizard", {record})
	// and an in-desk <a href="/desk/visit-wizard?record=..."> — which the router
	// intercepts and routes as the path alone — leave a bare /desk/visit-wizard
	// that Back, Forward or a reload would read as the picker. Such a target is
	// `unaddressed`, which no screen matches (target_key), so the load's
	// sync_route replaces that entry with the visit's own address.
	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== "visit-wizard") return null;
		const options = frappe.route_options || {};
		const take = (key) => {
			const value = options[key];
			delete options[key];
			return value == null ? "" : String(value);
		};
		const record = take("record");
		const step = take("step");
		if (frappe.route_options && !Object.keys(frappe.route_options).length) {
			frappe.route_options = null;
		}
		if (route[1]) {
			return { record: String(route[1]), step: String(route[2] || ""), feature: String(route[3] || "") };
		}
		const in_address = frappe.utils.get_url_arg("record") || "";
		const name = record || in_address;
		return {
			record: name,
			step: step || frappe.utils.get_url_arg("step") || "",
			feature: "",
			unaddressed: !!name && name !== in_address,
		};
	}

	// The screen on view, in the same shape as route_target().
	view_target(index) {
		if (!this.doc) return { record: "", step: "", feature: "" };
		const at = index == null ? this.step_index : index;
		const step = this.steps[at] || {};
		const tabbed = step.table && this.features.length;
		return { record: this.doc.name, step: step.key || "", feature: tabbed ? this.feature || "" : "" };
	}

	// One string per screen, so "is that the screen already showing?" is a
	// comparison. A visit's bare address is its first step, and a feature is
	// part of the screen only on a step that shows feature tabs. An address for
	// such a step without a feature keeps the tab already chosen, and is then
	// corrected in place to name it (sync_route) — it must not compare equal to
	// whichever tab happens to be showing, or a tab change would push nothing.
	// An `unaddressed` target (route_target) is no screen at all.
	target_key(target) {
		if (!target || !target.record || target.unaddressed) return "";
		const step = target.step || "safety";
		const tabbed = this.features.length && this.steps.some((s) => s.key === step && s.table);
		return [target.record, step, tabbed ? target.feature || "" : ""].join("/");
	}

	step_for(key) {
		const index = this.steps.findIndex((step) => step.key === key);
		return index < 0 ? 0 : index;
	}

	// Put `target` in the address as a new history entry, or in place of the
	// current one. Each entry the page pushes is tagged in history.state with
	// the screen it was pushed from, which is what lets the wizard's own Back
	// button step back through history (back_one_step) instead of stacking a
	// new entry on top.
	push_route(target, replace) {
		// Only while this page is the one on screen: a visit that finishes loading
		// after the technician went elsewhere must not route them back here.
		if ((frappe.get_route() || [])[0] !== "visit-wizard") return;
		const behind = this._url_target ? this.target_key(this._url_target) : null;
		const kept = ((window.history.state || {}).vz || {}).back;
		const before = window.location.href;
		// A leftover key would ride along into the route pushed here.
		if (frappe.route_options) {
			delete frappe.route_options.record;
			delete frappe.route_options.step;
		}
		const route = ["visit-wizard"];
		if (target.record) {
			route.push(target.record);
			if (target.step) route.push(target.step);
			if (target.step && target.feature) route.push(target.feature);
		}
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(route);
		// Read only by push_state, which has already run: frappe itself clears
		// the flag after 100 ms *and* every pending request, so left set it would
		// turn the technician's next Next (the safety brief still loading) into a
		// replace instead of a new entry.
		delete frappe.route_flags.replace_route;
		this._url_target = target;
		if (window.location.href === before) return;
		const back = replace ? kept : behind;
		if (back == null) return;
		try {
			window.history.replaceState(Object.assign({}, window.history.state, { vz: { back } }), "");
		} catch (e) {
			// Untagged, the wizard's Back button pushes rather than steps back.
		}
	}

	// Keep the address on the screen showing. A move the technician made gets
	// its own entry; a screen the address asked for and could not show as-is
	// (a later step before the safety tick, a step this visit does not have)
	// corrects the entry it is on rather than adding one.
	sync_route(from_route) {
		const view = this.view_target();
		if (this.target_key(this._url_target) === this.target_key(view)) return;
		this.push_route(view, from_route);
	}

	// A visit picked from the list is a screen change like any other — its own
	// entry, so Back returns to the list. Only from the list: an answer that
	// arrives after the technician has gone elsewhere must not drag them back.
	open_record(name) {
		if ((frappe.get_route() || [])[0] !== "visit-wizard") return;
		if (this.doc || (this._url_target && this._url_target.record)) return;
		this.push_route({ record: name, step: "", feature: "" });
	}

	// The wizard's own Back button. When the entry behind this one is the
	// previous step — it is whenever Next led here — go back through history
	// rather than pushing that step again: Next, Back, Next must not leave a
	// stack that the phone's Back button then replays.
	back_one_step() {
		const back = ((window.history.state || {}).vz || {}).back;
		if (back != null && back === this.target_key(this.view_target(this.step_index - 1))) {
			window.history.back();
			return;
		}
		this.go(this.step_index - 1);
	}

	// A move that came from Back/Forward has no tap to blur the field being
	// typed in, and the number and text inputs commit on "change" — so blur it
	// here first, or the reading or the note is dropped with the old screen.
	commit_input() {
		const el = document.activeElement;
		if (el && el !== document.body && this.$wrap[0] && this.$wrap[0].contains(el) && el.blur) {
			el.blur();
		}
	}

	// Before the visit on screen gives way to the list or another visit: commit
	// the field being typed in and send what is unsaved. Not awaited — Back must
	// not hang on a bad signal — so flush_save() keeps hold of the record it is
	// saving and parks a failure against it (see park()). Silent: by the time
	// the server answers, this visit is no longer the one on screen, and
	// frappe's "Visit Out of Date … Reload to continue" would sit over another
	// visit without saying which it meant; park() names it instead.
	leave_record() {
		if (!this.doc) return;
		this.commit_input();
		if (this._saving[this.doc.name] && this.has_dirty()) {
			// A save of this visit is still on the wire and the rest may only
			// follow it — which flush_save() would do for the visit on screen,
			// and this one is about to stop being that. Queue the rest behind it.
			this.park(this.doc, this.dirty, this.pending_added, { soon: true });
			this.dirty = { fields: {}, rows: {} };
			this.pending_added = [];
			return;
		}
		this.flush_save({ silent: true }).catch(() => {});
	}

	// Another Desk page took over. The visit stays loaded (Back returns to it),
	// so this is a plain flush — nothing is parked for a record still on screen.
	save_on_hide() {
		if (!this.doc) return;
		this.commit_input();
		this.flush_save().catch(() => {});
	}

	// Reload re-reads the screen the address names — the visit on the same
	// step, or the list — and drops edits not yet sent: it is the way out of
	// "Visit Out of Date", and of "Could not load this visit".
	reload() {
		const target = this.doc ? this.view_target() : this._url_target || { record: "" };
		this.reset();
		if (target.record) {
			this.load_record(target.record, target);
		} else {
			this.show_picker();
		}
	}

	// ----- picker --------------------------------------------------------

	show_picker() {
		const ticket = ++this._paint_seq;
		this.page.set_title(__("Visits"));
		this.page.clear_indicator();
		this.$wrap.html(`<div class="vz-empty">${__("Loading…")}</div>`);
		Promise.all([
			frappe.call("erpnext_enhancements.api.time_kiosk.get_my_visits_today"),
			frappe.call("erpnext_enhancements.api.maintenance_visit.get_upcoming_visits"),
			this.kept_records(),
		]).then(([today_res, upcoming_res, kept]) => {
			// A visit opened (or Forward pressed) while the list loaded wins.
			if (ticket !== this._paint_seq) return;
			const today = (today_res && today_res.message) || [];
			const upcoming = (upcoming_res && upcoming_res.message) || [];
			this.$wrap.empty();

			// What this phone holds that the server does not have yet (v1.596.0): first,
			// so nobody walks off thinking a visit with no signal at the fountain went in.
			if (kept.names.length) {
				this.$wrap.append(`<div class="vz-section-head">${__("Saved on this phone, not sent yet")}</div>`);
				kept.names.forEach((name) => this.$wrap.append(this.kept_card(name, kept.items)));
			}

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
		}, () => {
			if (ticket !== this._paint_seq) return;
			this.$wrap.html(`<div class="vz-empty">${__("Could not load your visits.")}</div>`);
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
										message: __(
											"This site already had an open form — opening that one. Check its date on the Wrap-up step."
										),
										indicator: "blue",
									});
								}
								dialog.hide();
								this.open_record(name);
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

	// A visit with something kept on this phone: what it is, and what is needed. One that
	// waits on the technician (a choice, something turned down, a visit that can no longer be
	// read) can also be discarded, after a confirmation.
	kept_card(name, items) {
		const mine = items.filter((item) => item.record === name);
		const photos = mine.filter((item) => item.kind === "photo" && !item.refused).length;
		const finish = mine.find((item) => item.kind === "finish");
		const bits = [];
		if (!vz_unsent_empty(vz_unsent_read(name))) bits.push(__("answers"));
		if (photos) bits.push(__("{0} photos", [photos]));
		if (finish && !finish.refused && !finish.blocked) bits.push(__("Finish"));
		let sub;
		let needs_you = true;
		if (finish && finish.refused) sub = __("Finishing it was turned down. Open it to finish it again.");
		else if (this._needs_you.has(name) || (finish && finish.blocked)) sub = __("Needs you: open it.");
		else if (mine.some((item) => item.refused)) sub = __("A photo was turned down. Open it to try again.");
		else {
			needs_you = false;
			sub = __("Waiting for signal: {0}", [bits.join(", ") || __("nothing left")]);
		}
		const $wrap = $("<div></div>");
		$(`<button class="vz-pick-card">
				<div class="vz-card-title">${frappe.utils.escape_html(name)}</div>
				<div class="vz-card-sub">${frappe.utils.escape_html(sub)}</div>
			</button>`)
			.on("click", () => this.open_record(name))
			.appendTo($wrap);
		if (needs_you) {
			$(`<button type="button" class="vz-link-btn">${__("Discard what this phone kept")}</button>`)
				.on("click", () =>
					frappe.confirm(
						__(
							"Throw away the answers, photos and Finish this phone kept for {0}? They have not reached the server.",
							[frappe.utils.escape_html(name)]
						),
						() => this.discard_kept(name).then(() => this.show_picker())
					)
				)
				.appendTo($wrap);
		}
		return $wrap;
	}

	today_card(visit) {
		const sub = visit.visit_label || visit.serial_no || __("Site visit");
		return $(`<button class="vz-pick-card">
				<div class="vz-card-title">${frappe.utils.escape_html(visit.project_title || visit.project)}</div>
				<div class="vz-card-sub">${frappe.utils.escape_html(sub)} · ${frappe.utils.escape_html(visit.name)}</div>
			</button>`).on("click", () => this.open_record(visit.name));
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
					this.open_record(name);
				})
				.catch(() => {
					$btn.prop("disabled", false).text(__("Do Visit Today"));
				});
		});
		return $card;
	}

	// ----- loading -------------------------------------------------------

	// `target` is the step (and feature tab) the address asked for.
	load_record(name, target) {
		this.reset();
		const ticket = ++this._paint_seq;
		this._loading = name;
		this._load_target = target || { record: name, step: "", feature: "" };
		this.$wrap.html(`<div class="vz-empty">${__("Loading visit…")}</div>`);
		let parked = null;
		// A save of this visit still on the wire would land after the bootstrap
		// was read, leaving the screen a version behind and the next autosave
		// refused as out of date. Let it land (or fail, and be parked) first.
		Promise.resolve(this._saving[name])
			// What the phone held when the page opened (a Finish waiting, photos) is known first.
			.then(() => this._outbox_ready)
			.then(() => {
				if (ticket !== this._paint_seq) return null;
				parked = this.take_parked(name);
				return frappe.call({
					method: "erpnext_enhancements.api.maintenance_visit.get_visit_bootstrap",
					args: { record: name },
				});
			})
			.then((r) => {
				// The technician moved on while this loaded: paint nothing, and
				// hand back any parked edits taken for it.
				if (ticket !== this._paint_seq) {
					if (parked) this.park(parked.doc, parked.dirty, parked.pending_added);
					return;
				}
				const data = r.message || {};
				this._loading = null;
				// An old name (v1.610.0 renamed the random names from before v1.597.0): the
				// server found the visit by it and answered with the new one. What this phone kept
				// moves to the new name, and the address is corrected in place, so Back still goes
				// where it went and the visit loads under its own name.
				const renamed = (data.record || {}).name;
				if (renamed && renamed !== name) {
					const held = parked;
					parked = null;
					return this.move_kept(name, renamed).then(() => {
						if (held) this.park({ ...held.doc, name: renamed }, held.dirty, held.pending_added, { soon: true });
						if (ticket !== this._paint_seq) return;
						const want = this._load_target || {};
						this.push_route({ record: renamed, step: want.step || "", feature: want.feature || "" }, true);
					});
				}
				this.doc = data.record;
				this.dashboard = data.dashboard || {};
				this.section_meta = data.sections || {};
				this.template_meta = data.template_meta || {};
				this.feature_names = data.features || {};
				this.sign_off = data.sign_off || {};
				this.apply_state(data.state);
				// What this phone kept for the visit (v1.596.0). Written from everything
				// this page held for it, parked edits and an unanswered choice included, so
				// it replaces them, and the choice is asked again.
				const kept = vz_unsent_read(name);
				delete this._held[name];
				this._needs_you.delete(name);
				if (this._handed.has(name)) {
					// Its Finish waits on this phone, unless it went through meanwhile.
					if (this.doc.docstatus === 0) {
						this.build_steps();
						this.render_finish_waiting();
						this.sync_route(true);
						this.schedule_sync(0);
						return;
					}
					this.drop_finish(name);
				}
				if (!this.restore_kept(kept) && parked) this.reapply_parked(parked);
				parked = null;
				this.say_kept_finish(name);
				this.retry_refused_photos(name);
				this.safety_ok = !!this.doc.safety_acknowledged;
				this.build_steps();
				this.build_features();
				const want = this._load_target || {};
				this.step_index = this.clamp_step(this.step_for(want.step));
				if (want.feature && this.features.includes(want.feature)) this.feature = want.feature;
				this.render();
				this.sync_route(true);
			})
			.catch(() => {
				if (parked) this.park(parked.doc, parked.dirty, parked.pending_added);
				if (ticket !== this._paint_seq) return;
				this._loading = null;
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

	// The order a technician actually works a visit in, for every template:
	// pH/ORP are read on arrival, before any cleaning or dosing moves them, and
	// the chemicals and supplies used are counted last, once nothing more will
	// be added. Between them, Cleaning and Inspection follow the template:
	// whichever has the earlier section there comes first, so a site's wrap-up
	// checks ("Chemicals used?") land after its cleaning list, and a template
	// that inspects before draining (Tiered Garden winterization) still does.
	// Without template positions (an untemplated or older record), Cleaning
	// goes first.
	build_steps() {
		this.steps = [{ key: "safety", title: __("Safety") }];
		if ((this.doc.chemistry_readings || []).length) {
			this.steps.push({ key: "readings", title: __("Water Chemistry"), table: "chemistry_readings" });
		}
		const middle = [
			{ key: "tasks", title: __("Cleaning"), table: "cleaning_tasks" },
			{ key: "results", title: __("Inspection"), table: "maintenance_results" },
		].filter((step) => (this.doc[step.table] || []).length);
		const first_position = (table) => {
			const positions = (this.doc[table] || [])
				.map((row) => ((this.section_meta || {})[row.section] || {}).order)
				.filter((order) => typeof order === "number");
			return positions.length ? Math.min(...positions) : Infinity;
		};
		// Array.prototype.sort is stable, so equal positions keep Cleaning first.
		middle.sort((a, b) => first_position(a.table) - first_position(b.table) || 0);
		this.steps.push(...middle);
		if ((this.doc.consumables || []).length) {
			this.steps.push({ key: "consumables", title: __("Chemicals Used"), table: "consumables" });
		}
		this.steps.push({ key: "wrapup", title: __("Wrap-up") });
	}

	build_features() {
		// Per Site Visit records tag every row with its water feature; one
		// header serial (or untagged rows) means no tab strip. A form ticked Once per
		// Site Visit (v1.634.0) leaves its rows untagged: with tagged rows beside them
		// they get a Whole site tab of their own, first, or they would show on no tab.
		const serials = new Set();
		let untagged = false;
		["chemistry_readings", "consumables", "maintenance_results", "cleaning_tasks"].forEach((table) => {
			(this.doc[table] || []).forEach((row) => {
				if (row.serial_no) serials.add(row.serial_no);
				else untagged = true;
			});
		});
		const tabbed = !this.doc.serial_no && (serials.size > 1 || (serials.size && untagged));
		this.features = tabbed ? (untagged ? [VZ_SITE] : []).concat(Array.from(serials)) : [];
		this.feature = this.features[0] || null;
	}

	rows(table) {
		const rows = this.doc[table] || [];
		return this.features.length ? rows.filter((row) => (row.serial_no || VZ_SITE) === this.feature) : rows;
	}

	// The water feature the tab on screen stands for: null on the Whole site tab and
	// when there are no tabs, so a row added there is not tagged with the sentinel.
	feature_serial() {
		return this.feature && this.feature !== VZ_SITE ? this.feature : null;
	}

	// ----- dirty tracking + autosave --------------------------------------

	set_field(field, value) {
		this.note_base(null, null, field, this.doc[field]);
		this.doc[field] = value;
		this.dirty.fields[field] = value;
		this.journal(this.doc.name);
		this.schedule_save();
	}

	set_row(table, row, field, value) {
		if (row.name) this.note_base(table, row.name, field, row[field]);
		row[field] = value;
		if (row.name) {
			const table_dirty = (this.dirty.rows[table] = this.dirty.rows[table] || {});
			(table_dirty[row.name] = table_dirty[row.name] || {})[field] = value;
		}
		// A consumable added here (no name yet) is kept whole, its new qty with it.
		this.journal(this.doc.name);
		this.schedule_save();
	}

	schedule_save() {
		clearTimeout(this._save_timer);
		this._save_timer = setTimeout(() => this.flush_save().catch(() => {}), 4000);
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
			// No signal, and the answers are kept on the phone (v1.596.0).
			offline: __("Saved on this phone - will send when signal returns"),
		};
		$el.toggleClass("vz-err", state === "error");
		$el.toggleClass("vz-offline", state === "offline");
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

	// Saves outlive the screen they were made on: this keeps hold of the record
	// it is saving (`doc`), because by the time the server answers Back may have
	// put the list or another visit on screen. options: `silent` keeps frappe
	// from showing a refusal itself (see leave_record).
	flush_save(options) {
		options = options || {};
		clearTimeout(this._save_timer);
		const doc = this.doc;
		if (!doc || doc.docstatus !== 0 || !this.has_dirty()) {
			return Promise.resolve();
		}
		// One save of a visit at a time. Each carries the `modified` the one
		// before it returned, so a second sent alongside the first is refused as
		// out of date. What is unsaved now goes out when the first has answered.
		if (this._saving[doc.name]) {
			return this._saving[doc.name].then(() => (this.doc === doc ? this.flush_save(options) : undefined));
		}
		const added_local = this.pending_added;
		const sent_dirty = this.dirty;
		const patch = vz_patch(sent_dirty, added_local);
		this.dirty = { fields: {}, rows: {} };
		this.pending_added = [];
		// Unsent until answered: still part of what the phone keeps (unsent()).
		this._inflight[doc.name] = { doc, dirty: sent_dirty, pending_added: added_local };
		this.set_save_state("saving");

		const done = frappe
			.call({
				method: "erpnext_enhancements.api.maintenance_visit.save_visit",
				args: {
					record: doc.name,
					patch: JSON.stringify(patch),
					modified: doc.modified,
				},
				silent: !!options.silent,
			})
			.then((r) => {
				const state = r.message || {};
				// The server names appended rows per table, in patch order (vz_patch).
				const by_table = {};
				added_local.forEach((row) => (by_table[vz_added_table(row)] = by_table[vz_added_table(row)] || []).push(row));
				Object.entries(state.added || {}).forEach(([table, names]) => {
					names.forEach((name, index) => {
						if ((by_table[table] || [])[index]) by_table[table][index].name = name;
					});
				});
				delete this._inflight[doc.name];
				this.rebase(doc.name, sent_dirty);
				if (doc !== this.doc) {
					// Nothing of it is on screen any more, but a later save of this
					// record (a parked one) must carry the version this one made.
					doc.modified = state.modified || doc.modified;
					this.journal(doc.name);
					this.after_signal();
					return;
				}
				this.apply_state(state);
				this.journal(doc.name);
				if (state.clock_cleared) {
					frappe.show_alert({
						message: __(
							"Clock times from another day were removed from this visit. The office can enter the real times."
						),
						indicator: "orange",
					});
				}
				this.refresh_reading_flags();
				this._retry_attempt = 0;
				this.set_save_state(this.has_dirty() ? "pending" : "saved");
				this.after_signal();
			})
			.catch((error) => {
				delete this._inflight[doc.name];
				if (doc !== this.doc) {
					// Never into the visit on screen now — these rows are not on it.
					// Anything parked for this record since is newer, so this goes under it.
					this.park(doc, sent_dirty, added_local, { underneath: true, refused: vz_refused(error) });
					return;
				}
				// keep the edits — newer in-flight changes win over the failed batch
				this.dirty = vz_merge_dirty(sent_dirty, this.dirty);
				this.pending_added = added_local.concat(this.pending_added);
				// No signal, and the phone holds every answer: say that, not "NOT SAVED".
				const kept = this.journal(doc.name);
				this.set_save_state(kept && vz_try_later(error) ? "offline" : "error");
				this.schedule_retry();
				throw error;
			});
		this.track_save(doc.name, done);
		return done;
	}

	// Record `promise` as the save of `name` on the wire, as one that never
	// rejects: flush_save() and load_record() wait on it, whatever its outcome.
	track_save(name, promise) {
		const tracked = promise.then(
			() => {},
			() => {}
		);
		this._saving[name] = tracked;
		tracked.then(() => {
			if (this._saving[name] === tracked) delete this._saving[name];
		});
	}

	// ----- saves that outlive the screen ---------------------------------------
	//
	// Back to the list or on to another visit while a save is failing (a bad
	// signal) used to drop the edits: reset() cleared them, and a failure that
	// answered later was merged into whichever visit was on screen by then. Now
	// they are parked against their own record, retried there with that
	// record's own `modified`, and put back on screen if the visit is opened
	// again before they land. beforeunload still warns while any are parked.
	//
	// Every save sent from here is silent (see leave_record for why), and a
	// refusal is not retried: the likeliest one is "out of date" after a save
	// that did commit but whose answer was lost on a bad signal, and a retry
	// carries the same stale `modified`, so it could never succeed — it used to
	// pop six unnamed "Visit Out of Date" dialogs over whatever visit was open,
	// where tapping that page's Reload would drop *that* visit's unsent edits.

	// options: `underneath` — this batch is older than anything already parked
	// for the record; `attempt` — sends already failed; `soon` — not a failure,
	// just queued behind a save still on the wire, so send it straight after;
	// `refused` — the server turned it down (vz_refused): kept, not retried.
	park(doc, dirty, pending_added, options) {
		options = options || {};
		const name = doc.name;
		const parked = this._parked[name] || { doc, dirty: { fields: {}, rows: {} }, pending_added: [], attempt: 0 };
		parked.dirty = options.underneath ? vz_merge_dirty(dirty, parked.dirty) : vz_merge_dirty(parked.dirty, dirty);
		parked.pending_added = options.underneath
			? pending_added.concat(parked.pending_added)
			: parked.pending_added.concat(pending_added);
		parked.attempt = Math.max(parked.attempt, options.attempt || 0);
		this._parked[name] = parked;
		this.journal(name);
		clearTimeout(parked.timer);
		if (options.refused) {
			// Opening the visit again loads it fresh and puts these back on
			// screen as unsaved edits, which then save against that version.
			frappe.show_alert({
				message: __(
					"Changes to {0} were turned down by the server. They are kept: open that visit to send them again.",
					[name]
				),
				indicator: "red",
			});
			return;
		}
		if (!options.soon && !this._park_warned.has(name)) {
			this._park_warned.add(name);
			frappe.show_alert({
				message: __("Changes to {0} are not saved yet. Retrying.", [name]),
				indicator: "orange",
			});
		}
		if (parked.attempt >= 6) {
			frappe.show_alert({
				message: __("Changes to {0} are still not saved. Open that visit to try again.", [name]),
				indicator: "red",
			});
			return;
		}
		const delay = options.soon ? 0 : 1000 * Math.pow(2, Math.min(parked.attempt + 1, 5));
		parked.timer = setTimeout(() => this.send_parked(name), delay);
	}

	send_parked(name) {
		if (!this._parked[name]) return;
		clearTimeout(this._parked[name].timer);
		let parked = null;
		// Taken only once any save of this record still on the wire has answered,
		// so a failure it parks meanwhile is in the batch too, underneath.
		const sent = Promise.resolve(this._saving[name]).then(() => {
			parked = this.take_parked(name);
			if (!parked) return null;
			this._inflight[name] = parked;
			return frappe.call({
				method: "erpnext_enhancements.api.maintenance_visit.save_visit",
				args: {
					record: name,
					patch: JSON.stringify(vz_patch(parked.dirty, parked.pending_added)),
					modified: parked.doc.modified,
				},
				silent: true,
			});
		});
		this.track_save(
			name,
			sent.then(
				(r) => {
					if (!parked) return;
					delete this._inflight[name];
					this.rebase(name, parked.dirty);
					parked.doc.modified = ((r && r.message) || {}).modified || parked.doc.modified;
					this._park_warned.delete(name);
					this.journal(name);
					frappe.show_alert({ message: __("Saved your changes to {0}.", [name]), indicator: "green" });
					this.after_signal();
				},
				(error) => {
					if (!parked) return;
					delete this._inflight[name];
					this.park(parked.doc, parked.dirty, parked.pending_added, {
						underneath: true,
						attempt: parked.attempt + 1,
						refused: vz_refused(error),
					});
				}
			)
		);
	}

	take_parked(name) {
		const parked = this._parked[name];
		if (!parked) return null;
		delete this._parked[name];
		clearTimeout(parked.timer);
		return parked;
	}

	// Parked edits back onto the freshly loaded record, as unsaved edits the
	// normal autosave sends. A row gone from the record since has nowhere to go.
	reapply_parked(parked) {
		Object.entries(parked.dirty.fields).forEach(([field, value]) => {
			this.doc[field] = value;
			this.dirty.fields[field] = value;
		});
		Object.entries(parked.dirty.rows).forEach(([table, by_name]) => {
			Object.entries(by_name).forEach(([name, changes]) => {
				const row = (this.doc[table] || []).find((candidate) => candidate.name === name);
				if (!row) return;
				Object.assign(row, changes);
				const table_dirty = (this.dirty.rows[table] = this.dirty.rows[table] || {});
				table_dirty[name] = { ...(table_dirty[name] || {}), ...changes };
			});
		});
		parked.pending_added.forEach((row) => {
			const table = vz_added_table(row);
			(this.doc[table] = this.doc[table] || []).push(row);
			this.pending_added.push(row);
		});
		if (this.has_dirty()) this.schedule_save();
	}

	// ----- kept on this phone (v1.596.0) -------------------------------------------
	//
	// See the note above vz_storage. What follows keeps the copy on the phone in step with
	// what this page holds, puts it back when a visit is opened, and sends what waited for
	// signal (photos, a Finish, and the answers of a visit no longer on screen).

	// A Finish kept on this phone that could not be sent: say why, on opening the visit. A
	// submitted visit has nothing left to finish.
	say_kept_finish(name) {
		const finish = this._finish[name];
		if (!finish) return;
		if (this.doc.docstatus !== 0) {
			this.drop_finish(name);
			return;
		}
		if (finish.refused) {
			frappe.msgprint(
				finish.reason
					? __("Finishing this visit was turned down by the server: {0} Fix that and tap Finish again.", [
							frappe.utils.escape_html(finish.reason),
						])
					: __("Finishing this visit was turned down by the server. Fix what it asked for and tap Finish again.")
			);
		} else if (finish.blocked && !this._choice) {
			frappe.msgprint(__("This visit's Finish was not sent. Check its answers, then tap Finish again."));
		}
	}

	// Photos of this visit the server turned down get another try on opening it, and are
	// never let go unseen: turned down again, they stay kept and marked, for the technician
	// to retake (a retake replaces them) or discard from the list.
	retry_refused_photos(name) {
		return vz_outbox_items()
			.then((items) =>
				Promise.all(
					items
						.filter((item) => item.kind === "photo" && item.record === name && item.refused)
						.map((item) => {
							const again = { ...item };
							delete again.refused;
							this._photo_waiting.add(again.key);
							return vz_outbox_put(again);
						})
				)
			)
			.then(() => (this._photo_waiting.size ? this.send_photos(name) : null));
	}

	// Before a field's first unsent change, remember the server's value, so a kept answer
	// can later tell "unchanged on the server" from "someone else changed it".
	note_base(table, row_name, field, old) {
		const name = this.doc && this.doc.name;
		if (!name || this.unsent_has(name, table, row_name, field)) return;
		const base = (this._base[name] = this._base[name] || { fields: {}, rows: {} });
		if (table) {
			const rows = (base.rows[table] = base.rows[table] || {});
			(rows[row_name] = rows[row_name] || {})[field] = old === undefined ? null : old;
		} else {
			base.fields[field] = old === undefined ? null : old;
		}
	}

	// A save landed: for anything in it that was edited again meanwhile, the server's value
	// is now what that save sent, so that is the before-value of the edit still unsent.
	rebase(name, landed) {
		const base = this._base[name];
		if (!base) return;
		Object.entries(landed.fields).forEach(([field, value]) => {
			if (this.unsent_has(name, null, null, field)) base.fields[field] = value;
		});
		Object.entries(landed.rows).forEach(([table, by_name]) => {
			Object.entries(by_name).forEach(([row_name, changes]) => {
				Object.entries(changes).forEach(([field, value]) => {
					if (!this.unsent_has(name, table, row_name, field)) return;
					const rows = (base.rows[table] = base.rows[table] || {});
					(rows[row_name] = rows[row_name] || {})[field] = value;
				});
			});
		});
	}

	unsent_has(name, table, row_name, field) {
		const parts = [this._inflight[name], this._parked[name], this._held[name] ? { dirty: this._held[name] } : null];
		if (this.doc && this.doc.name === name) parts.push({ dirty: this.dirty });
		return parts.some((part) => {
			if (!part) return false;
			if (!table) return field in part.dirty.fields;
			return field in (((part.dirty.rows || {})[table] || {})[row_name] || {});
		});
	}

	// Everything of visit `name` the server does not have yet, oldest first: the save on the
	// wire, edits parked against it, and the edits on screen, made against version
	// `modified`; and apart from them, the kept answers the technician was asked about and
	// has not chosen on (`held`), which are asked about again whatever the version.
	unsent(name) {
		let dirty = { fields: {}, rows: {} };
		let added = [];
		const on_screen = this.doc && this.doc.name === name;
		const parts = [
			this._inflight[name],
			this._parked[name],
			on_screen ? { dirty: this.dirty, pending_added: this.pending_added } : null,
		];
		parts.forEach((part) => {
			if (!part) return;
			dirty = vz_merge_dirty(dirty, part.dirty);
			added = added.concat(part.pending_added || []);
		});
		const holder =
			(on_screen && this.doc) || (this._parked[name] || {}).doc || (this._inflight[name] || {}).doc || null;
		return {
			dirty,
			// A row the server has named is on it.
			pending_added: added.filter((row) => !row.name),
			held: this._held[name] || null,
			modified: holder ? holder.modified : null,
			// The rows this phone knows by name, per table that takes added rows: never taken for
			// one it added (vz_reconcile).
			known: holder
				? Object.fromEntries(
						Object.keys(VZ_ADDED).map((table) => [
							table,
							(holder[table] || []).map((row) => row.name).filter(Boolean),
						])
					)
				: {},
		};
	}

	// Write what visit `name` has unsent to this phone, or forget it once there is none.
	// True when the phone holds it. Not while the visit loads (this page does not hold all
	// of it then) or while its Finish waits here (the copy on the phone is the visit's).
	journal(name) {
		if (!name || this._loading === name || this._handed.has(name)) return false;
		const unsent = this.unsent(name);
		if (vz_unsent_empty(unsent)) {
			delete this._base[name];
			vz_unsent_write(name, null);
			this._journal_failed = false;
			return false;
		}
		const kept = vz_unsent_write(name, {
			record: name,
			modified: unsent.modified,
			dirty: unsent.dirty,
			pending_added: vz_plain_added(unsent.pending_added),
			held: unsent.held,
			known: unsent.known,
			base: vz_prune_base(this._base[name], unsent.dirty),
			at: Date.now(),
		});
		// Storage full or refused: nothing is kept, and closing the tab warns (beforeunload).
		this._journal_failed = !kept;
		return kept;
	}

	// The visit just loaded: put back what this phone kept for it (see vz_reconcile). True
	// when it held anything. Answers the server has since changed differently wait on the
	// technician's choice (render_choice) and stay kept, as `held`, until then.
	restore_kept(entry) {
		const doc = this.doc;
		const name = doc.name;
		if (vz_unsent_empty(entry)) return false;
		if (doc.docstatus !== 0) {
			vz_unsent_write(name, null);
			frappe.msgprint(
				__(
					"This phone kept answers for {0} that never reached the server, and the visit has been submitted since, so they could not be added. Tell the office if something is missing.",
					[name]
				)
			);
			return true;
		}
		const plan = vz_reconcile(entry, doc);
		this._base[name] = vz_prune_base(entry.base, entry.dirty);
		plan.apply.forEach((change) => this.put_back(change));
		plan.added.forEach((row) => {
			const copy = { ...row };
			const table = vz_added_table(copy);
			(doc[table] = doc[table] || []).push(copy);
			this.pending_added.push(copy);
		});
		const back = plan.apply.length + plan.added.length;
		if (plan.conflicts.length) {
			const held = { fields: {}, rows: {} };
			plan.conflicts.forEach((change) => vz_put_change(held, change));
			this._held[name] = held;
			this._choice = { record: name, conflicts: plan.conflicts };
			this._needs_you.add(name);
		}
		this.journal(name);
		if (this.has_dirty()) this.schedule_save();
		if (back) {
			frappe.show_alert({
				message: __("{0} answers kept on this phone were put back, and are being sent.", [back]),
				indicator: "blue",
			});
		}
		return true;
	}

	// One kept answer back onto the visit as an unsent edit.
	put_back(change) {
		const doc = this.doc;
		if (!change.table) {
			doc[change.field] = change.value;
			this.dirty.fields[change.field] = change.value;
			return;
		}
		const row = (doc[change.table] || []).find((candidate) => candidate.name === change.row);
		if (!row) return;
		row[change.field] = change.value;
		const rows = (this.dirty.rows[change.table] = this.dirty.rows[change.table] || {});
		(rows[change.row] = rows[change.row] || {})[change.field] = change.value;
	}

	// Kept answers the server has since changed differently: say which, and let the
	// technician pick. Nothing else shows until they do (render() comes here), so nothing
	// on screen can be answered on top of a value that is still in question. Leaving without
	// choosing keeps them, and they are asked about again next time.
	render_choice() {
		const choice = this._choice;
		const label = (change) => {
			if (!change.table) return change.field.replace(/_/g, " ");
			const row = (this.doc[change.table] || []).find((r) => r.name === change.row) || {};
			const what = row.reading || row.question || row.task || row.item_name || row.item || change.table;
			const where =
				row.serial_no && this.features.length ? ` (${this.feature_names[row.serial_no] || row.serial_no})` : "";
			const main = {
				chemistry_readings: "reading_value",
				maintenance_results: "selection",
				cleaning_tasks: "is_done",
				consumables: "qty",
			};
			return main[change.table] === change.field
				? what + where
				: `${what}${where}: ${change.field.replace(/_/g, " ")}`;
		};
		const shown = (value) => (value === null || value === undefined || value === "" ? __("(blank)") : String(value));
		const lines = choice.conflicts
			.map(
				(change) =>
					`<li><b>${frappe.utils.escape_html(label(change))}</b>: ${__("this phone")} <b>${frappe.utils.escape_html(
						shown(change.value)
					)}</b>, ${__("the server")} <b>${frappe.utils.escape_html(shown(change.server))}</b></li>`
			)
			.join("");
		this.$wrap.empty().append(`
			<div class="vz-banner vz-banner-blue">
				<h6>${__("Answers kept on this phone")}</h6>
				${__(
					"This phone kept answers for this visit that did not reach the server, and since then the server's copy of these has changed (someone else edited the visit). Which should the visit keep?"
				)}
				<ul class="vz-choice-list">${lines}</ul>
			</div>
		`);
		$(`<button type="button" class="vz-choice-btn vz-choice-phone">${__("Use this phone's answers")}</button>`)
			.on("click", () => this.resolve_kept(true))
			.appendTo(this.$wrap);
		$(`<button type="button" class="vz-choice-btn vz-choice-server">${__("Keep the server's")}</button>`)
			.on("click", () => this.resolve_kept(false))
			.appendTo(this.$wrap);
	}

	resolve_kept(use_phone) {
		const choice = this._choice;
		if (!choice || !this.doc || this.doc.name !== choice.record) return;
		const name = choice.record;
		delete this._held[name];
		this._choice = null;
		this._needs_you.delete(name);
		if (use_phone) {
			// The phone's answer, chosen over what the server has now: that is the value it
			// replaces, should this edit itself be kept and compared again.
			choice.conflicts.forEach((change) => {
				const row = change.table ? (this.doc[change.table] || []).find((r) => r.name === change.row) : null;
				const now = change.table ? (row || {})[change.field] : this.doc[change.field];
				this.note_base(change.table, change.row, change.field, now);
				this.put_back(change);
			});
			if (this.has_dirty()) this.schedule_save();
		}
		this.journal(name);
		// A Finish held back for this choice (sync_record) goes now, with what was chosen.
		const finish = this._finish[name];
		if (finish && finish.blocked && !finish.refused) {
			this.keep_finish(this.doc, finish.signature, finish.technician_signature, finish.email_sign_off);
			return;
		}
		this.render();
	}

	// The Finish kept for a visit, as this page sees it and on the phone. A sendable one makes
	// the visit wait for signal (`_handed`). One the server turned down, or held back for a
	// choice, does not: the visit opens, and can be fixed and finished again.
	set_finish(item) {
		this._finish[item.record] = item;
		if (item.refused || item.blocked) this._handed.delete(item.record);
		else this._handed.add(item.record);
		return vz_outbox_put(item);
	}

	drop_finish(name) {
		delete this._finish[name];
		this._handed.delete(name);
		return vz_outbox_delete(vz_finish_key(name));
	}

	// A Finish with no signal (finish() could not reach the server): keep it, with the
	// signatures (the client's, and the technician's since v1.610.0), on this phone. The
	// visit's answers are kept with it; from here the copy on the phone is the visit's, and
	// sync_stored sends the answers, then the photos, then the Finish, when signal returns.
	// Nothing is handed over unless the phone holds all of it. The client's sign-off address
	// (v1.614.0) is kept with it, so the email goes when the Finish does, once.
	keep_finish(doc, signature, technician_signature, email_sign_off) {
		const name = doc.name;
		const cannot = () =>
			frappe.msgprint(
				__(
					"There is no signal, and this browser cannot keep the Finish on the phone. Tap Finish again when you have signal."
				)
			);
		if (!vz_storage()) return Promise.resolve(cannot());
		// A save of this visit still on the wire answers first, so whatever it leaves unsent is
		// in the copy kept below, and nothing on this screen is sent after it.
		return Promise.resolve(this._saving[name]).then(() => {
			const unsent = this.unsent(name);
			if (!this.journal(name) && !vz_unsent_empty(unsent)) return cannot();
			const item = {
				key: vz_finish_key(name),
				kind: "finish",
				record: name,
				signature: signature || null,
				technician_signature: technician_signature || null,
				email_sign_off: email_sign_off || null,
				at: Date.now(),
			};
			return this.set_finish(item).then(
				(kept) => {
					if (!kept) {
						this.drop_finish(name);
						return cannot();
					}
					if (this.doc === doc) {
						clearTimeout(this._save_timer);
						clearTimeout(this._retry_timer);
						this.dirty = { fields: {}, rows: {} };
						this.pending_added = [];
						this.render_finish_waiting();
					}
					this.schedule_sync(0);
				},
				() => {
					this.drop_finish(name);
					cannot();
				}
			);
		});
	}

	render_finish_waiting() {
		this.set_save_state("idle");
		this.$wrap.empty().append(`
			<div class="vz-done-screen">
				<div class="vz-done-icon">📱</div>
				<h4>${__("Saved on this phone")}</h4>
				<p class="vz-muted">${__(
					"There is no signal here. This visit, its photos and the Finish will be sent as soon as there is. You can close this page; opening the Visit Wizard again sends them too."
				)}</p>
			</div>
		`);
		$(`<button type="button" class="vz-pick-card" style="text-align:center;font-weight:600;">${__("Back to Today's Visits")}</button>`)
			.on("click", () => this.push_route({ record: "", step: "", feature: "" }))
			.appendTo(this.$wrap.find(".vz-done-screen"));
	}

	// The page's "online" event: send the answers on screen now rather than at the next
	// retry, then everything else that waited.
	signal_back() {
		const doc = this.doc;
		if (doc && doc.docstatus === 0 && this.editing(doc.name) && this.has_dirty()) {
			this.flush_save().catch(() => {});
		}
		this.after_signal();
	}

	// A call got through, so there is signal: send the photos of the visit on screen and
	// whatever is kept for other visits. Nothing is read or sent when nothing is kept.
	after_signal() {
		const doc = this.doc;
		if (doc && doc.docstatus === 0 && this.editing(doc.name) && this._photo_waiting.size) {
			this.send_photos(doc.name);
		}
		if (this._handed.size || this._photo_waiting.size || vz_unsent_names().length) this.schedule_sync(0);
	}

	schedule_sync(delay) {
		clearTimeout(this._sync_timer);
		this._sync_timer = setTimeout(() => this.sync_stored(), delay || 0);
	}

	// Visits with something kept on this phone: answers, photos or a Finish.
	kept_records() {
		return vz_outbox_items().then((items) => {
			const names = new Set(vz_unsent_names());
			items.forEach((item) => names.add(item.record));
			return { names: [...names], items };
		});
	}

	// The visit is open on screen and being worked on: its answers are the autosave's.
	editing(name) {
		return !!(this.doc && this.doc.name === name && !this._handed.has(name));
	}

	// Send everything kept for visits this page is not working on, one visit at a time,
	// then try again on a backoff while anything sendable is left (15 s, doubling to 2
	// minutes). A visit waiting on the technician (a choice, a refusal) is not retried.
	sync_stored() {
		if (this._sync) return this._sync;
		clearTimeout(this._sync_timer);
		const run = this.kept_records()
			.then(({ names, items }) =>
				names.reduce(
					(chain, name) =>
						chain.then(() => {
							if (this.editing(name)) return this.send_photos(name);
							if (!this.sync_ready(name)) return null;
							return this.sync_record(name, items.filter((item) => item.record === name)).then(
								() => null,
								() => null
							);
						}),
					Promise.resolve()
				)
			)
			.then(() => this.kept_records())
			.then(({ names, items }) => {
				this._sync = null;
				const waiting = names.filter((name) => this.sync_wants(name, items));
				if (!waiting.length) {
					this._sync_attempt = 0;
					return;
				}
				this._sync_attempt = Math.min(this._sync_attempt + 1, 4);
				this.schedule_sync(15000 * Math.pow(2, this._sync_attempt - 1));
			});
		this._sync = run.catch(() => {
			this._sync = null;
		});
		return this._sync;
	}

	// Nothing of `name` is held in this page's memory (that copy is sent by the autosave, the
	// parked saves and the photo runs), and nobody needs to choose first.
	sync_ready(name) {
		if (this._loading === name || this._parked[name] || this._inflight[name] || this._photo_runs[name]) {
			return false;
		}
		if (this._needs_you.has(name) || this._held[name]) return false;
		return !this.editing(name);
	}

	// Worth trying again later: something sendable kept for a visit the background sender
	// may send. Not one waiting on the technician, or on this page's own retries.
	sync_wants(name, items) {
		const sendable = items.filter((item) => item.record === name && !item.refused && !item.blocked);
		if (this.editing(name)) return sendable.some((item) => item.kind === "photo");
		if (!this.sync_ready(name)) return false;
		return sendable.length > 0 || !vz_unsent_empty(vz_unsent_read(name));
	}

	// One visit not on screen: against the server's version now (get_visit_state, which
	// changes nothing), its kept answers, then each photo (its url kept with the answers
	// before the photo is let go), one save, then the Finish. No signal stops it, to try again
	// later. Anything the server answers with an error stops it for good and is said: the
	// visit then opens normally, and a Finish waiting for it is held back until it is
	// finished again (or, after a choice, sent with it).
	sync_record(name, items) {
		const photos = items.filter((item) => item.kind === "photo" && !item.refused).sort((a, b) => a.at - b.at);
		const finish = items.find((item) => item.kind === "finish" && !item.refused && !item.blocked);
		const entry = vz_unsent_read(name);
		if (vz_unsent_empty(entry) && !photos.length && !finish) {
			if (entry) vz_unsent_write(name, null);
			return Promise.resolve();
		}
		const stuck = (message) => {
			this._needs_you.add(name);
			frappe.show_alert({ message, indicator: "red" });
			if (finish) return this.set_finish({ ...finish, blocked: 1 });
			return null;
		};
		const read = () =>
			frappe.call({
				method: "erpnext_enhancements.api.maintenance_visit.get_visit_state",
				args: { record: name },
				silent: true,
			});
		const work = read().then(
			(r) => {
				const data = (r && r.message) || {};
				const doc = data.record || {};
				// Renamed on the server (v1.610.0): the server found the visit by its old name and
				// answered with the new one. What was kept moves to the new name and is sent there.
				if (doc.name && doc.name !== name) {
					return this.move_kept(name, doc.name).then(() => {
						if (!this.sync_ready(doc.name)) return null;
						return vz_outbox_items().then((all) =>
							this.sync_record(
								doc.name,
								all.filter((item) => item.record === doc.name)
							)
						);
					});
				}
				// An answer without the visit is no answer: never read as "submitted".
				if (doc.name !== name) return Promise.reject(new Error(`no state for ${name}`));
				let modified = (data.state || {}).modified || doc.modified;
				if (doc.docstatus !== 0) return this.settle_finished(name, entry, photos, finish);
				const plan = vz_reconcile(entry || {}, doc);
				if (plan.conflicts.length) {
					return stuck(
						__("Answers kept on this phone for {0} differ from the server's. Open that visit to choose.", [name])
					);
				}
				const patch = { fields: {}, rows: {} };
				plan.apply.forEach((change) => vz_put_change(patch, change));
				return photos
					.reduce(
						(chain, item) =>
							chain.then(() =>
								vz_upload(item.blob, item.filename, name, item.table ? null : item.field).then(
									(url) => {
										const change = vz_photo_change(item, url);
										vz_put_change(patch, change);
										// Kept with the answers before the photo is let go (read afresh: the
										// page may have written since).
										vz_keep_change(vz_unsent_read(name), name, change, modified);
										this._photo_waiting.delete(item.key);
										return vz_outbox_delete(item.key);
									},
									(error) => (vz_try_later(error) ? Promise.reject(error) : this.photo_refused(item))
								)
							),
						Promise.resolve()
					)
					.then(() => {
						if (vz_unsent_empty({ dirty: patch, pending_added: plan.added })) return modified;
						return frappe
							.call({
								method: "erpnext_enhancements.api.maintenance_visit.save_visit",
								args: { record: name, patch: JSON.stringify(vz_patch(patch, plan.added)), modified },
								silent: true,
							})
							.then(
								(saved) => {
									vz_unsent_write(name, null);
									frappe.show_alert({ message: __("Saved your changes to {0}.", [name]), indicator: "green" });
									return ((saved && saved.message) || {}).modified || modified;
								},
								(error) => {
									if (!vz_try_later(error)) {
										stuck(
											__("Changes to {0} were turned down by the server. They are kept: open that visit.", [name])
										);
									}
									return Promise.reject(error);
								}
							);
					})
					.then((now) => {
						modified = now;
						if (!finish) return null;
						return frappe
							.call({
								method: "erpnext_enhancements.api.maintenance_visit.finish_visit",
								args: {
									record: name,
									signature: finish.signature,
									technician_signature: finish.technician_signature || null,
									email_sign_off: finish.email_sign_off || null,
									modified,
								},
								silent: true,
							})
							.then(
								(done) => this.finish_sent(name, (done && done.message) || {}),
								(error) => {
									if (vz_try_later(error)) return Promise.reject(error);
									// What the server said, kept with the Finish and said when it is opened: a
									// Finish kept before v1.610.0 has no technician's signature, and is refused.
									const reason = vz_server_message(error);
									// Answered with an error. Most likely it went through already and its
									// answer was lost: the visit says which.
									return read().then((again) => {
										const state = ((again && again.message) || {}).record || {};
										if (
											state.docstatus !== 0 ||
											(state.workflow_state && state.workflow_state !== "Draft")
										) {
											return this.finish_sent(name, state);
										}
										return this.set_finish({ ...finish, refused: 1, reason }).then(() =>
											frappe.show_alert({
												message: reason
													? __("Finishing {0} was turned down by the server: {1} Open that visit and finish it again.", [
															name,
															frappe.utils.escape_html(reason),
														])
													: __(
															"Finishing {0} was turned down by the server. Open that visit and finish it again.",
															[name]
														),
												indicator: "red",
											})
										);
									});
								}
							);
					});
			},
			(error) => {
				if (!vz_try_later(error)) {
					stuck(
						__(
							"Visit {0} could not be read (deleted, or no longer yours). What this phone kept for it is listed under Saved on this phone.",
							[name]
						)
					);
				}
				return Promise.reject(error);
			}
		);
		this.track_save(name, work);
		return work;
	}

	// A sign-off email that could not go with the Finish (v1.614.0): the visit went anyway, so
	// this is said, not raised. Sent, it is on the done screen (finish_now) or needs no word.
	say_sign_off_sent(state) {
		if (!state || !state.sign_off_error) return;
		frappe.msgprint({
			title: __("Sign-off email not sent"),
			message: frappe.utils.escape_html(state.sign_off_error),
			indicator: "orange",
		});
	}

	// The Finish went through (now, or before, with its answer lost).
	finish_sent(name, state) {
		this.say_sign_off_sent(state);
		const submitted = state.docstatus === 1;
		frappe.show_alert({
			message: submitted ? __("Visit {0} submitted.", [name]) : __("Visit {0} sent for review.", [name]),
			indicator: "green",
		});
		if (this.doc && this.doc.name === name) {
			if (state.modified) this.doc.modified = state.modified;
			if (state.docstatus != null) this.doc.docstatus = state.docstatus;
			if (state.workflow_state !== undefined) this.doc.workflow_state = state.workflow_state;
			this.$wrap.find(".vz-done-icon").text(submitted ? "✅" : "📨");
			this.$wrap.find(".vz-done-screen h4").text(submitted ? __("Visit submitted") : __("Sent for review"));
			this.$wrap.find(".vz-done-screen p").text(__("Everything kept on this phone has been sent."));
		}
		return this.drop_finish(name);
	}

	// The visit was submitted while this phone held things for it: nothing more can go on
	// it. A Finish is done; answers and photos are said, not silently dropped.
	settle_finished(name, entry, photos, finish) {
		const lost = (vz_unsent_empty(entry) ? 0 : 1) + photos.length;
		vz_unsent_write(name, null);
		if (lost) {
			frappe.msgprint(
				__(
					"Visit {0} was submitted before this phone could send everything it kept for it ({1} photos). Tell the office if something is missing.",
					[name, photos.length]
				)
			);
		}
		photos.forEach((item) => this._photo_waiting.delete(item.key));
		return Promise.all([
			finish || this._finish[name] ? this.drop_finish(name) : null,
			...photos.map((item) => vz_outbox_delete(item.key)),
		]);
	}

	// A visit renamed on the server (v1.610.0: the random names from before v1.597.0 became
	// PRJ-…-V###, the old one kept as its legacy_name). Everything this phone kept under the old
	// name (answers, photos, a Finish, and what this page holds for it) moves to the new name,
	// so it is sent to that visit instead of being reported as deleted. Kept answers already
	// under the new name are merged with the old ones (vz_merge_kept), the new name's winning.
	move_kept(old, current) {
		if (!old || !current || old === current) return Promise.resolve();
		const rekey = (key) => {
			const parts = String(key).split("|");
			if (parts[1] === old) parts[1] = current;
			return parts.join("|");
		};
		// Answers already kept under the new name too (opened by it, then the old name's turned
		// up): one entry, the old one underneath. Left as two, the old name would stay listed and
		// be read again on every retry, for ever.
		const entry = vz_unsent_read(old);
		if (entry) {
			const there = vz_unsent_read(current);
			const moved = there ? vz_merge_kept(entry, there) : { ...entry, record: current };
			if (vz_unsent_write(current, moved)) vz_unsent_write(old, null);
		}
		[this._held, this._base, this._parked].forEach((by_name) => {
			if (!(old in by_name) || current in by_name) return;
			by_name[current] = by_name[old];
			delete by_name[old];
		});
		const parked = this._parked[current];
		if (parked && parked.doc && parked.doc.name === old) {
			parked.doc = { ...parked.doc, name: current };
			clearTimeout(parked.timer);
			parked.timer = setTimeout(() => this.send_parked(current), 0);
		}
		[this._handed, this._needs_you, this._photo_again].forEach((names) => {
			if (names.delete(old)) names.add(current);
		});
		this._photo_waiting = new Set([...this._photo_waiting].map(rekey));
		return vz_outbox_items().then((items) =>
			Promise.all(
				items
					.filter((item) => item.record === old)
					.map((item) => {
						const moved = { ...item, key: rekey(item.key), record: current };
						return vz_outbox_put(moved)
							.then(() => vz_outbox_delete(item.key))
							.then(() => {
								if (moved.kind !== "finish") return;
								delete this._finish[old];
								this._finish[current] = moved;
							});
					})
			)
		);
	}

	// Throw away what this phone kept for a visit, at the technician's word (the list's
	// Discard): for a visit that can no longer take it (deleted, reassigned, turned down).
	discard_kept(name) {
		// After any send of it still on the wire, which could otherwise write it back.
		return Promise.resolve(this._saving[name]).then(() => this.discard_now(name));
	}

	discard_now(name) {
		vz_unsent_write(name, null);
		delete this._held[name];
		delete this._base[name];
		this._needs_you.delete(name);
		this.take_parked(name);
		return vz_outbox_items()
			.then((items) =>
				Promise.all(
					items
						.filter((item) => item.record === name)
						.map((item) => {
							this._photo_waiting.delete(item.key);
							return vz_outbox_delete(item.key);
						})
				)
			)
			.then(() => {
				delete this._finish[name];
				this._handed.delete(name);
			});
	}

	// Photos of the visit on screen, kept on this phone, uploaded oldest first, one at a time.
	// One run per visit: a photo taken during a run goes in the next one. No signal ends the
	// run and the photos stay for the next; a visit left meanwhile is the background
	// sender's after it.
	send_photos(name) {
		if (this._photo_runs[name]) {
			this._photo_again.add(name);
			return this._photo_runs[name];
		}
		const run = vz_outbox_items()
			.then((items) =>
				items
					.filter((item) => item.kind === "photo" && item.record === name && !item.refused)
					.sort((a, b) => a.at - b.at)
			)
			.then((items) => items.reduce((chain, item) => chain.then(() => this.send_photo(item)), Promise.resolve()))
			.catch(() => {})
			.then(() => {
				delete this._photo_runs[name];
				if (this._photo_again.delete(name)) return this.send_photos(name);
				if (!this.editing(name)) this.schedule_sync(0);
				return null;
			});
		this._photo_runs[name] = run;
		return run;
	}

	send_photo(item) {
		return vz_upload(item.blob, item.filename, item.record, item.table ? null : item.field).then(
			(url) => {
				// On the answer first (kept on the phone with the rest), then the blob goes.
				this.record_photo(item, url);
				this._photo_waiting.delete(item.key);
				frappe.show_alert({ message: __("Photo attached."), indicator: "green" });
				return vz_outbox_delete(item.key);
			},
			(error) => (vz_try_later(error) ? Promise.reject(error) : this.photo_refused(item))
		);
	}

	// An uploaded photo's url onto its answer: on screen as an edit; else onto the visit's
	// parked save, or its answers kept on the phone.
	record_photo(item, url) {
		const change = vz_photo_change(item, url);
		if (this.editing(item.record)) {
			if (!change.table) {
				this.set_field(change.field, url);
				return;
			}
			const row = (this.doc[change.table] || []).find((candidate) => candidate.name === change.row);
			if (row) this.set_row(change.table, row, change.field, url);
			return;
		}
		const parked = this._parked[item.record];
		if (parked) {
			vz_put_change(parked.dirty, change);
			this.journal(item.record);
			return;
		}
		vz_keep_change(vz_unsent_read(item.record), item.record, change, null);
	}

	// The server turned a photo down (too big, not allowed): retrying now cannot help. It is
	// kept, marked, and said; opening the visit tries it once more (retry_refused_photos).
	photo_refused(item) {
		this._photo_waiting.delete(item.key);
		frappe.show_alert({
			message: __("A photo for {0} was turned down by the server. Take it again, or send it to the office.", [
				item.record,
			]),
			indicator: "red",
		});
		return vz_outbox_put({ ...item, refused: 1 });
	}

	// A photo taken for a row: kept on this phone first (v1.596.0), then uploaded. With no
	// signal it waits there and goes when there is, page reloaded or not. A browser that
	// cannot keep it uploads straight away, as before. It replaces a photo of the same row
	// still waiting. With no table and no row, it is `field` on the visit itself (v1.597.0:
	// the fountain as the technician left it).
	take_photo(table, row, file, $btn, field) {
		const doc = this.doc;
		field = field || "photo";
		// Strictly increasing, so two photos in one millisecond still get their own keys.
		const at = (this._photo_at = Math.max(Date.now(), (this._photo_at || 0) + 1));
		const prefix = vz_photo_prefix(doc.name, table || "-", row ? row.name : field);
		const item = {
			key: prefix + at,
			kind: "photo",
			record: doc.name,
			table: table || null,
			row: row ? row.name : null,
			field,
			blob: file,
			filename: file.name,
			at,
		};
		return vz_outbox_put(item).then(
			(kept) => {
				if (!kept) return this.upload_photo_now(doc, table, row, file, $btn, field);
				this._photo_waiting.add(item.key);
				$btn.addClass("vz-photo-waiting");
				return vz_outbox_items()
					.then((items) =>
						Promise.all(
							items
								.filter((other) => other.kind === "photo" && other.key.startsWith(prefix) && other.at < at)
								.map((other) => {
									this._photo_waiting.delete(other.key);
									return vz_outbox_delete(other.key);
								})
						)
					)
					.then(() => this.send_photos(doc.name))
					.then(() => {
						if (this._photo_waiting.has(item.key) && this.doc === doc) {
							frappe.show_alert({
								message: __("No signal: the photo is saved on this phone and will upload when there is."),
								indicator: "orange",
							});
						}
					});
			},
			() => this.upload_photo_now(doc, table, row, file, $btn, field)
		);
	}

	// The camera button's class: a photo attached, or one waiting on this phone. With no
	// table, `row` is the visit and the photo is its `field`.
	photo_class(table, row, field) {
		field = field || "photo";
		if ((row || {})[field]) return "vz-has-photo";
		if (!this.doc) return "";
		const prefix = vz_photo_prefix(this.doc.name, table || "-", table ? row.name : field);
		return [...this._photo_waiting].some((key) => key.startsWith(prefix)) ? "vz-photo-waiting" : "";
	}

	// The upload as it was before v1.596.0, for a browser that cannot keep the photo.
	upload_photo_now(doc, table, row, file, $btn, field) {
		vz_upload(file, file.name, doc.name, table ? null : field).then(
			(file_url) => {
				if (this.doc !== doc) return;
				if (table) this.set_row(table, row, field || "photo", file_url);
				else this.set_field(field, file_url);
				$btn.addClass("vz-has-photo");
				frappe.show_alert({ message: __("Photo attached."), indicator: "green" });
			},
			() => frappe.show_alert({ message: __("Photo upload failed."), indicator: "red" })
		);
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
		// Kept answers that clash with the server's are settled before anything else shows.
		if (this._choice && this.doc && this._choice.record === this.doc.name) {
			this.render_choice();
			return;
		}
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
		// A form filled in for an earlier day says so on every step, so nobody sends it for
		// review believing it is today's.
		if (!readonly && this.doc.visit_date && this.doc.visit_date < frappe.datetime.get_today()) {
			this.$wrap.append(
				`<div class="vz-banner vz-banner-blue">${__("Filling in the visit of {0}. Change the date on the Wrap-up step.", [
					frappe.utils.escape_html(frappe.datetime.str_to_user(this.doc.visit_date)),
				])}</div>`
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
			const label = serial === VZ_SITE ? __("Whole site") : (this.feature_names || {})[serial] || serial;
			// Outstanding required rows for this feature on this step, so a tab
			// that still needs work says so before you leave the site.
			const left = (this.doc[table] || []).filter(
				(row) => (row.serial_no || VZ_SITE) === serial && row.is_mandatory && !vz_row_answered(table, row)
			).length;
			$(`<button type="button" role="tab" aria-selected="${active}" class="vz-tab ${active ? "vz-active" : ""}">
					${frappe.utils.escape_html(label)}${left ? ` <span class="vz-chip vz-chip-req">${left}</span>` : ""}
				</button>`)
				.on("click", () => this.go(this.step_index, { feature: serial }))
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
				.on("click", () => this.back_one_step())
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

	// Every step change: the Back/Next buttons, the step dots, a feature tab,
	// and Back/Forward (from_route, via handle_route). The move is shown first
	// and the address follows it, so a push that frappe dedupes never leaves
	// the screen behind.
	go(index, options) {
		options = options || {};
		if (!this.doc) return;
		const target = this.clamp_step(index, !options.from_route);
		const feature =
			options.feature && this.features.includes(options.feature) ? options.feature : this.feature;
		if (target !== this.step_index || feature !== this.feature) {
			this.commit_input();
			this.flush_save().catch(() => {});
			this.step_index = target;
			this.feature = feature;
			this.render();
			window.scrollTo(0, 0);
		}
		this.sync_route(options.from_route);
	}

	// The checklist opens only after the safety acknowledgement. The Start
	// Visit button always knew that; the step dots, Forward and a typed address
	// did not. A submitted visit is read-only, so it is not held back.
	clamp_step(index, tell) {
		const step = Math.max(0, Math.min(index, this.steps.length - 1));
		if (step > 0 && !this.safety_ok && this.doc.docstatus === 0) {
			if (tell) {
				frappe.show_alert({
					message: __("Acknowledge the safety procedures first."),
					indicator: "orange",
				});
			}
			return 0;
		}
		return step;
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
		this.render_agreement();
		this.render_last_visit();

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
		this.render_safety_brief();
		this.render_report_incident();
	}

	// "What the agreement covers" (v1.619.0): the site's maintenance agreement, so the
	// technician sees the schedule the client was promised, the custom plan (Exhibit A) this
	// visit carries out, and the work the agreement leaves out, which is a follow-up for a
	// quote rather than a free extra. Collapsed, with the schedule in its header. From the
	// dashboard context (sapphire_maintenance/agreement_scope.py), which leaves out rates and
	// sends nothing without a maintenance agreement the user may read. Read-only.
	render_agreement() {
		const scope = this.dashboard.agreement;
		if (!scope || !scope.name) return;
		const esc = (value) => frappe.utils.escape_html(String(value == null ? "" : value));
		const row = (label, value) =>
			value ? `<div class="vz-scope-row"><b>${esc(label)}</b>${vz_plain_html(value)}</div>` : "";
		let body =
			row(__("Visits"), scope.schedule) +
			row(__("Days"), scope.preferred_days) +
			row(__("Time window"), scope.time_window) +
			row(__("Spring startup"), scope.startup) +
			row(__("Winterization"), scope.winterization) +
			row(__("Services chosen"), (scope.services || []).join("\n"));
		if (scope.custom_plan) {
			body += `<div class="vz-last-feature">${__("Custom plan (Exhibit A)")}</div><div>${vz_plain_html(
				scope.custom_plan
			)}</div>`;
		}
		if (scope.billed_on_top) {
			body += `<div class="vz-last-feature">${__("Billed on top")}</div><div>${esc(scope.billed_on_top)}</div>`;
		}
		const left_out = scope.not_included || [];
		if (left_out.length) {
			body += `<div class="vz-last-feature">${__("Not included: raise a follow-up for a quote")}</div>${left_out
				.map((text) => `<div>• ${esc(text)}</div>`)
				.join("")}`;
		}
		const summary = [scope.schedule, scope.time_window].filter(Boolean).join(" · ");
		const $card = $(`
			<div class="vz-help vz-agreement">
				<button type="button" class="vz-help-head">
					<span>📄 ${__("What the agreement covers")}</span>
					<span class="vz-last-summary">${esc(summary)}</span>
					<span class="vz-help-caret">▸</span>
				</button>
				<div class="vz-help-body">${body}</div>
			</div>
		`);
		$card.find(".vz-help-head").on("click", () => $card.toggleClass("vz-open"));
		this.$wrap.append($card);
	}

	// "Last visit here" (v1.596.0, TASK-2026-02482): technicians used to start each visit
	// blind. The site's previous approved visit, from the dashboard context the wizard
	// already loads (visit_compare.last_visit, the helper behind "Compared with last
	// visit"): its readings with the out-of-range ones marked, anything found Fail or
	// Replace, and the notes, by water feature on a site visit. Collapsed, with what
	// matters in its header, so the Safety step stays short. Read-only.
	render_last_visit() {
		const last = this.dashboard.last_visit;
		if (!last) return;
		const esc = (value) => frappe.utils.escape_html(String(value == null ? "" : value));
		const number = (value) => String(Math.round(Number(value) * 1000) / 1000);
		let summary;
		let body;
		if (last.first_visit) {
			summary = __("First recorded visit");
			body = `<div class="vz-muted">${__("No earlier approved visit here to look back at.")}</div>`;
		} else {
			const groups = last.groups || [];
			const flagged = groups.reduce((n, g) => n + g.readings.filter((r) => r.out_of_range).length, 0);
			const problems = groups.reduce((n, g) => n + g.problems.length, 0);
			const day = last.day ? frappe.datetime.str_to_user(last.day) : "";
			summary = [day, last.technician_name].filter(Boolean).join(" · ");
			if (flagged) summary += ` · ${__("{0} out of range", [flagged])}`;
			if (problems) summary += ` · ${__("{0} to look at", [problems])}`;
			const named = groups.length > 1 || groups.some((g) => g.serial_no && !this.doc.serial_no);
			body = groups
				.map((group) => {
					let html = named ? `<div class="vz-last-feature">${esc(group.label || __("Whole site"))}</div>` : "";
					group.readings.forEach((r) => {
						html += `<div class="vz-last-line${r.out_of_range ? " vz-last-bad" : ""}"><span>${esc(r.reading)}</span><span>${esc(
							number(r.value)
						)}${r.uom ? " " + esc(r.uom) : ""}${r.out_of_range ? " · " + __("out of range") : ""}</span></div>`;
					});
					group.problems.forEach((p) => {
						html += `<div class="vz-last-line vz-last-bad"><span>${esc(p.question)}</span><span>${esc(p.answer)}</span></div>`;
						if (p.details) html += `<div class="vz-muted">${vz_plain_html(p.details)}</div>`;
					});
					return html;
				})
				.join("");
			if (last.notes) {
				body += `<div class="vz-last-feature">${__("Notes")}</div><div>${vz_plain_html(last.notes)}</div>`;
			}
			if (!body) body = `<div class="vz-muted">${__("Nothing was recorded on that visit.")}</div>`;
		}
		const $card = $(`
			<div class="vz-help vz-last">
				<button type="button" class="vz-help-head">
					<span>🕘 ${__("Last visit here")}</span>
					<span class="vz-last-summary">${esc(summary)}</span>
					<span class="vz-help-caret">▸</span>
				</button>
				<div class="vz-help-body">${body}</div>
			</div>
		`);
		$card.find(".vz-help-head").on("click", () => $card.toggleClass("vz-open"));
		this.$wrap.append($card);
	}

	// The brief, assembled at the moment: the SDS for the chemicals actually on
	// this visit, the PPE for this kind of work, and anything a previous
	// technician found here.
	//
	// It lands on the EXISTING safety step rather than a new screen. That step is
	// already there, nobody can proceed past it, and a second safety screen is one
	// people learn to click through twice as fast.
	//
	// Rendered async and appended when it arrives: the step must draw immediately
	// with the red banner, and the brief is an addition to it rather than a
	// precondition for it.
	render_safety_brief() {
		const $slot = $('<div class="vz-brief"></div>');
		this.$wrap.append($slot);
		frappe
			.call({
				method: "erpnext_enhancements.hr_enhancements.hazards.safety_brief",
				args: {
					maintenance_record: this.doc && this.doc.name,
					customer: this.doc && this.doc.customer,
					work_type: (this.template_meta && this.template_meta.work_type) || null,
				},
			})
			.then((r) => this.paint_safety_brief($slot, (r && r.message) || {}))
			.catch(() => {
				// A brief that cannot be assembled must not break the step. The red
				// banner and the tick are the load-bearing part and are already drawn.
			});
	}

	paint_safety_brief($slot, brief) {
		const esc = (v) => frappe.utils.escape_html(v || "");

		// Hazards first. Somebody already found these here, and that is the most
		// perishable thing on the screen.
		const hazards = brief.hazards || [];
		if (hazards.length) {
			$slot.append(`
				<div class="vz-banner vz-banner-red">
					<h6>${__("Found here before")}</h6>
					${hazards
						.map(
							(h) =>
								`${esc(h.category)}${h.where_exactly ? ` — ${esc(h.where_exactly)}` : ""}: ${esc(h.what)}${
									h.status === "Accepted risk" ? ` <i>(${__("known, not being fixed")})</i>` : ""
								}`
						)
						.join("<br>")}
				</div>
			`);
		}

		const ppe = brief.ppe || [];
		if (ppe.length) {
			$slot.append(`
				<div class="vz-banner vz-banner-blue">
					<h6>${__("PPE for this work")}</h6>
					${ppe.map((p) => `${esc(p.protection)}${p.because ? ` — ${esc(p.because)}` : ""}`).join("<br>")}
				</div>
			`);
		}

		const sheets = brief.sheets || [];
		if (sheets.length) {
			$slot.append(`
				<div class="vz-banner vz-banner-blue">
					<h6>${__("Chemicals on this visit")}</h6>
					${sheets
						.map(
							(c) =>
								`<b>${esc(c.label)}</b>${c.summary ? ` — ${esc(c.summary)}` : ""} ${
									c.document
										? `<a href="${esc(c.document)}" target="_blank">${__("safety sheet")}</a>`
										: `<i>${__("no safety sheet on file")}</i>`
								}`
						)
						.join("<br>")}
				</div>
			`);
		}

		// Reporting one, from the same screen. Thirty seconds standing at the thing.
		const $link = $(`
			<div class="vz-report-incident">
				<a href="#" data-hazard="1">${__("Report a hazard you found")}</a>
			</div>
		`).on("click", "a", (e) => {
			e.preventDefault();
			this.open_hazard_dialog();
		});
		$slot.append($link);
	}

	open_hazard_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("What did you find?"),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "category",
					label: __("Kind"),
					reqd: 1,
					options: [
						"Electrical",
						"Chemical",
						"Slip, trip or fall",
						"Confined space",
						"Structural",
						"Equipment",
						"Other",
					].join("\n"),
				},
				{ fieldtype: "Small Text", fieldname: "what", label: __("What it is"), reqd: 1 },
				{
					fieldtype: "Data",
					fieldname: "where_exactly",
					label: __("Where exactly"),
					description: __("The next person sent here sees this before they start."),
				},
			],
			primary_action_label: __("Send it"),
			primary_action: (values) => {
				dialog.hide();
				frappe.call({
					method: "erpnext_enhancements.hr_enhancements.hazards.report_hazard",
					args: Object.assign({}, values, {
						customer: this.doc && this.doc.customer,
						maintenance_record: this.doc && this.doc.name,
					}),
					freeze: true,
					callback: () => {
						frappe.show_alert({
							message: __("Logged. The next person here will see it."),
							indicator: "green",
						});
					},
				});
			},
		});
		dialog.show();
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
					<button type="button" aria-label="${__("Photo")}" class="vz-photo-btn ${this.photo_class("chemistry_readings", row)}">📷</button>
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
					<button type="button" class="vz-step-btn" data-dir="-1" aria-label="${__("Less")}">−</button>
					<div class="vz-qty">${row.qty || 0}</div>
					<button type="button" class="vz-step-btn" data-dir="1" aria-label="${__("More")}">+</button>
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
				// The dose unit, as the template's Chemical Dosing lines carry one: a visit
				// records what went into the fountain ("1 cap full"), not what left stock.
				{
					fieldname: "uom",
					fieldtype: "Data",
					label: __("Unit"),
					description: __("What you counted in, e.g. tablet, cap full, tablespoon, bottle."),
				},
			],
			primary_action_label: __("Add"),
			primary_action: (values) => {
				dialog.hide();
				const row = {
					item: values.item,
					item_name: values.item,
					qty: values.qty,
					uom: (values.uom || "").trim() || null,
					qty_step: 1,
					serial_no: this.feature_serial(),
				};
				this.doc.consumables.push(row);
				this.pending_added.push(row);
				this.journal(this.doc.name);
				// Drawn whether or not the save got through (a failed one shows as
				// NOT SAVED and retries), and only onto the visit it was added to.
				const doc = this.doc;
				this.flush_save()
					.catch(() => {})
					.then(() => {
						if (this.doc === doc) this.render();
					});
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
					<button type="button" aria-label="${__("Photo")}" class="vz-photo-btn ${this.photo_class("maintenance_results", row)}">📷</button>
					<button type="button" class="vz-follow-btn" style="display:none;">＋ ${__("Follow-up")}</button>
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
			// A Fail or Replace is often a part to order (v1.600.0): one tap to a follow-up.
			$extra.find(".vz-follow-btn").toggle(negative(row.selection) && this.doc.docstatus === 0);
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
		$extra.find(".vz-follow-btn").on("click", () =>
			this.add_follow_up({
				description: [row.question, row.answer].filter(Boolean).join(": "),
				source_question: row.question,
				serial_no: row.serial_no,
			})
		);
		sync_extra();
		return $card;
	}

	// ----- step: cleaning ---------------------------------------------------

	render_tasks() {
		this.render_step_groups("cleaning_tasks", (row) => this.task_card(row));
	}

	// Tap the task for done, N/A for a task this visit does not need (v1.598.0), and a note for
	// anything worth saying, above all why a task was not done: Lisa, on the Myers report, "It
	// only states that one cartridge wasn't cleaned out; why wasn't it?" The note was on the
	// form all along, but the card was a single tap with nowhere to type.
	task_card(row) {
		const $card = $(`
			<div class="vz-card vz-task-card">
				<div class="vz-check-card">
					<div class="vz-check-box"></div>
					<div style="flex:1;">
						<div class="vz-card-title">${frappe.utils.escape_html(row.task || "")} <span class="vz-req-slot"></span></div>
					</div>
					<button type="button" class="vz-na-btn">${__("N/A")}</button>
				</div>
				<div class="vz-row-extra">
					<input type="text" value="">
					<button type="button" aria-label="${__("Photo")}" class="vz-photo-btn ${this.photo_class("cleaning_tasks", row)}">📷</button>
				</div>
			</div>
		`);
		const $note = $card.find(".vz-row-extra input").val(row.notes || "");
		const paint = () => {
			const state = vz_task_state(row);
			$card.toggleClass("vz-done", state === "done").toggleClass("vz-na", state === "na");
			$card.find(".vz-check-card").toggleClass("vz-on", state === "done");
			$card.find(".vz-check-box").text(state === "done" ? "✓" : "");
			$card.find(".vz-na-btn").toggleClass("vz-on", state === "na");
			$card.find(".vz-req-slot").html(vz_required_chip("cleaning_tasks", row));
			$note.attr(
				"placeholder",
				state === "done"
					? __("Note (optional)")
					: state === "na"
						? __("Why it isn't needed (optional)")
						: __("Not done? Say why")
			);
		};
		// Done and N/A each clear the other: a task is one or the other, or neither. Both fields
		// are written on every tap, whatever this phone thinks they hold: answers kept on the
		// phone are judged field by field against the server (vz_reconcile), so a Done kept here
		// while the office marked the task N/A is asked about only if the phone also says "not
		// N/A". And the server settles a row ticked both ways without telling the phone.
		const set_state = (state) => {
			this.set_row("cleaning_tasks", row, "is_done", state === "done" ? 1 : 0);
			this.set_row("cleaning_tasks", row, "not_applicable", state === "na" ? 1 : 0);
			paint();
		};
		$card.find(".vz-check-card").on("click", () => set_state(vz_task_state(row) === "done" ? "open" : "done"));
		$card.find(".vz-na-btn").on("click", (event) => {
			event.stopPropagation();
			set_state(vz_task_state(row) === "na" ? "open" : "na");
		});
		$note.on("change", (event) => {
			this.set_row("cleaning_tasks", row, "notes", event.target.value);
			paint();
		});
		this.bind_photo($card.find(".vz-photo-btn"), "cleaning_tasks", row);
		paint();
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

		this.render_visit_date();
		this.render_not_done();

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
		this.render_follow_ups();
		this.render_fountain_photo();
		this.render_technician_signature();

		const $sig_card = $(`
			<div class="vz-card">
				<div class="vz-card-title">${__("Client Sign-off")}</div>
				<div class="vz-card-sub">${__("Optional — have the client sign below.")}</div>
				<canvas class="vz-sig"></canvas>
				<button type="button" class="vz-link-btn">${__("Clear signature")}</button>
			</div>
		`);
		this.setup_signature($sig_card, "client_sign_off");
		this.render_sign_off_email($sig_card);
		this.$wrap.append($sig_card);
	}

	// The client isn't on site (v1.614.0). Nik, 2026-10-13: "will this signature requirement
	// allow the tech to send it via email to the client to sign off? the clients aren't always
	// available to sign in person". Ticked, Finish sends the address with the visit, and the
	// server emails the link once the visit has gone for review (finish_visit's
	// email_sign_off), so a Finish kept on the phone without signal sends it when it goes. It
	// never holds the visit up: the client's sign-off is not needed for review or approval. A
	// signature drawn on the pad above wins, and nothing is emailed.
	render_sign_off_email($card) {
		const doc = this.doc;
		if (!doc || doc.docstatus !== 0 || doc.client_sign_off) return;
		const choice = this._email_sign_off;
		if (choice.email === null) choice.email = this.sign_off.default_email || "";
		const request = this.sign_off.request;
		const out = request && ["Sent", "Viewed"].includes(request.status);
		const sent_args = out
			? [frappe.utils.escape_html(request.email || ""), frappe.datetime.str_to_user(request.sent_on)]
			: [];
		const sent = !out
			? ""
			: request.status === "Viewed"
				? __("A sign-off link was emailed to {0} on {1}, and the client has opened it.", sent_args)
				: __("A sign-off link was emailed to {0} on {1}.", sent_args);
		const $block = $(`
			<div class="vz-sign-off-email-block">
				${sent ? `<div class="vz-card-sub vz-sign-off-sent">${sent}</div>` : ""}
				<label class="vz-sign-off-email-toggle">
					<input type="checkbox" ${choice.on ? "checked" : ""}>
					<span>${out ? __("Email a new link to sign off (replaces the one sent)") : __("Client not here? Email them a link to sign off")}</span>
				</label>
				<div class="vz-sign-off-email" style="${choice.on ? "" : "display:none;"}">
					<input class="vz-email" type="email" inputmode="email" autocomplete="off" placeholder="${__("client@example.com")}">
					<div class="vz-card-sub">${__("Sent when you finish. The visit goes for review either way.")}</div>
				</div>
			</div>
		`);
		$block.find(".vz-email").val(choice.email);
		$block.find(".vz-sign-off-email-toggle input").on("change", (event) => {
			choice.on = !!event.target.checked;
			$block.find(".vz-sign-off-email").toggle(choice.on);
		});
		$block.find(".vz-email").on("input change", (event) => {
			choice.email = String(event.target.value || "").trim();
		});
		$card.append($block);
	}

	// The address Finish sends, or null: not ticked, or the client signed on the pad.
	email_sign_off_value() {
		const choice = this._email_sign_off;
		if (!choice.on || this.pad_value("client_sign_off") || (this.doc && this.doc.client_sign_off)) return null;
		return String(choice.email || "").trim();
	}

	// The technician signs the visit they are finishing (v1.610.0). Nik, 2026-10-13: "we need
	// the technicians to sign the maintenance visits once they complete it so the printed form
	// has that." Signed by whoever finishes it, so a substitute covering the site signs as
	// themselves; the server stamps the signer and the time. Required to send the visit for
	// review (finish_now asks, and the server refuses without it). A visit already waiting
	// for review, or submitted, shows the signature it has: a reviewer never signs here.
	render_technician_signature() {
		const doc = this.doc;
		const signing = this.technician_finishing();
		const full_name = (user) => {
			if (user === frappe.session.user) return frappe.session.user_fullname || user;
			const info = frappe.user_info ? frappe.user_info(user) : null;
			return (info && info.fullname) || user;
		};
		const signed_by = doc.technician_signed_by
			? __("Signed by {0}", [full_name(doc.technician_signed_by)]) +
				(doc.technician_signed_on ? ` · ${frappe.datetime.str_to_user(doc.technician_signed_on)}` : "")
			: "";
		const me = frappe.session.user_fullname || frappe.session.user;
		const $card = $(`
			<div class="vz-card vz-tech-sig">
				<div class="vz-card-title">${__("Technician signature")}</div>
				<div class="vz-card-sub">${
					signing
						? frappe.utils.escape_html(__("Signing as {0}. Sign to finish the visit.", [me]))
						: frappe.utils.escape_html(signed_by || __("Not signed."))
				}</div>
				${
					signing
						? `<canvas class="vz-sig"></canvas>
							<div class="vz-sig-needed" style="display:none;">${__("Sign here before you finish the visit.")}</div>
							<button type="button" class="vz-link-btn">${__("Clear signature")}</button>`
						: doc.technician_signature
							? `<img class="vz-sig-img" src="${encodeURI(String(doc.technician_signature))}" alt="">`
							: ""
				}
			</div>
		`);
		if (signing) this.setup_signature($card, "technician_signature");
		this.$wrap.append($card);
	}

	// Finishing sends the visit for review (or, with no workflow, submits it): the technician's
	// Finish, which needs their signature. Not a reviewer's Approve & Submit.
	technician_finishing() {
		return !!this.doc && this.doc.docstatus === 0 && this.doc.workflow_state !== "Pending Review";
	}

	// A technician finishing with no signature drawn and none on the visit already.
	missing_technician_signature() {
		return this.technician_finishing() && !this.pad_value("technician_signature") && !this.doc.technician_signature;
	}

	// What a pad holds for Finish to send: a signature drawn on this visit, else nothing (one
	// already on the visit is not sent again).
	pad_value(field) {
		const pad = this._pads[field];
		if (!pad || !pad.drawn) return null;
		return pad.data || (pad.canvas ? pad.canvas.toDataURL("image/png") : null);
	}

	// Every cleaning task left not done, with nothing said about why (v1.598.0): the
	// customer's report prints it as "Not done", and the office is the one asked why. A
	// reason here is the task's own note, the same field as on the card. Answered tasks
	// stay listed until the step is drawn again, so a reason typed is not snatched away.
	// Every water feature's tasks on a site visit, not only the tab on screen.
	render_not_done() {
		const open = (this.doc.cleaning_tasks || []).filter((row) => vz_task_unexplained(row));
		if (!open.length) return;
		const $card = $(`
			<div class="vz-card vz-why">
				<div class="vz-card-title">${__("Tasks not done")}</div>
				<div class="vz-card-sub">${__(
					"The customer's report shows these as not done. Say why for each, or go back and mark it done or N/A."
				)}</div>
			</div>
		`);
		open.forEach((row) => {
			const where =
				row.serial_no && this.features.length
					? ` (${this.feature_names[row.serial_no] || row.serial_no})`
					: "";
			const $row = $(`
				<div>
					<div class="vz-why-task">${frappe.utils.escape_html((row.task || "") + where)}</div>
					<div class="vz-row-extra"><input type="text" placeholder="${__("Why wasn't it done?")}"></div>
				</div>
			`);
			$row.find("input")
				.val(row.notes || "")
				.on("change", (event) => this.set_row("cleaning_tasks", row, "notes", event.target.value));
			$card.append($row);
		});
		this.$wrap.append($card);
	}

	// Follow-ups and parts needed (v1.600.0): what the site needs after today, for the office
	// to take on. Lisa: "say Austin talked to someone and told them you need a new manifold,
	// and then he adds it to the form with a follow up and then I can get it over to Brian".
	// Each becomes a tracked Maintenance Follow-Up when the visit is approved. Added here they
	// are kept on the phone like any answer until the server has them; a photo can be added
	// once one has been saved (a photo is kept by the row's name).
	render_follow_ups() {
		const rows = this.doc.follow_ups || [];
		const $card = $(`
			<div class="vz-card">
				<div class="vz-card-title">${__("Follow-ups and parts needed")}</div>
				<div class="vz-card-sub">${__(
					"Anything the site needs after today: a part to order, work to come back for. Say who on site you told. The office follows each one through."
				)}</div>
				<div class="vz-follow-ups"></div>
			</div>
		`);
		const $list = $card.find(".vz-follow-ups");
		rows.forEach((row) => {
			const part = row.item ? (row.qty ? `${row.item} × ${row.qty}` : row.item) : "";
			const where =
				row.serial_no && this.features.length
					? this.feature_names[row.serial_no] || row.serial_no
					: "";
			const details = [part, where, row.told ? __("Told: {0}", [row.told]) : ""].filter(Boolean).join(" · ");
			const $row = $(`
				<div class="vz-follow-up">
					<div class="vz-follow-up-text">${frappe.utils.escape_html(row.description || "")}${
						details ? `<small>${frappe.utils.escape_html(details)}</small>` : ""
					}${row.name ? "" : `<small>${__("A photo can be added once it has saved.")}</small>`}</div>
					${row.name ? `<button type="button" aria-label="${__("Photo")}" class="vz-photo-btn ${this.photo_class("follow_ups", row)}">📷</button>` : ""}
				</div>
			`);
			if (row.name && this.doc.docstatus === 0) this.bind_photo($row.find(".vz-photo-btn"), "follow_ups", row);
			$list.append($row);
		});
		if (this.doc.docstatus === 0) {
			$(`<button type="button" class="vz-add-btn">＋ ${__("Add a follow-up")}</button>`)
				.on("click", () => this.add_follow_up())
				.appendTo($card);
		}
		this.$wrap.append($card);
	}

	// `from` prefills it from an inspection answer: its question, the details typed, and its
	// water feature.
	add_follow_up(from) {
		from = from || {};
		// The Whole site tab is the select's own "Whole site" option, not a feature.
		const features = this.features
			.filter((serial) => serial !== VZ_SITE)
			.map((serial) => ({ label: this.feature_names[serial] || serial, value: serial }));
		const fields = [
			{
				fieldname: "description",
				fieldtype: "Small Text",
				label: __("What is needed"),
				reqd: 1,
				default: from.description || "",
			},
			{ fieldname: "item", fieldtype: "Link", label: __("Part (if you know it)"), options: "Item" },
			{ fieldname: "qty", fieldtype: "Float", label: __("Qty") },
			{
				fieldname: "told",
				fieldtype: "Data",
				label: __("Who on site did you tell?"),
				description: __("A name or a role, e.g. the property manager."),
			},
		];
		if (features.length) {
			fields.push({
				fieldname: "serial_no",
				fieldtype: "Select",
				label: __("Water feature"),
				options: [{ label: __("Whole site"), value: "" }, ...features],
				default: from.serial_no || this.feature_serial() || "",
			});
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Add a follow-up"),
			fields,
			primary_action_label: __("Add"),
			primary_action: (values) => {
				const description = (values.description || "").trim();
				if (!description) return;
				dialog.hide();
				const row = {
					_table: "follow_ups",
					description,
					item: values.item || null,
					qty: values.qty ? Number(values.qty) : null,
					told: (values.told || "").trim() || null,
					serial_no: (features.length ? values.serial_no : from.serial_no) || null,
					source_question: from.source_question || null,
				};
				(this.doc.follow_ups = this.doc.follow_ups || []).push(row);
				this.pending_added.push(row);
				this.journal(this.doc.name);
				const doc = this.doc;
				this.flush_save()
					.catch(() => {})
					.then(() => {
						if (this.doc === doc) this.render();
					});
			},
		});
		dialog.show();
	}

	// The fountain as the technician leaves it (v1.597.0): Lisa asked for a picture of the
	// customer's fountain on their visit report, and a photo of it running as we left is the
	// one worth sending. Kept on this phone like any photo until it uploads.
	render_fountain_photo() {
		const photo = this.doc.fountain_photo;
		const $card = $(`
			<div class="vz-card">
				<div class="vz-card-title">${__("The fountain as you leave it")}</div>
				<div class="vz-card-sub">${__("A photo of it running. It goes on the customer's visit report.")}</div>
				${photo ? `<img class="vz-fountain-photo" src="${encodeURI(String(photo))}" alt="">` : ""}
				<div style="margin-top:8px;">
					<button type="button" aria-label="${__("Photo")}" class="vz-photo-btn ${this.photo_class(null, this.doc, "fountain_photo")}">📷</button>
				</div>
			</div>
		`);
		if (this.doc.docstatus === 0) this.bind_photo($card.find(".vz-photo-btn"), null, null, "fountain_photo");
		this.$wrap.append($card);
	}

	// The day the work was done (Nik, 2026-10-12: workers "need to be able to historically
	// submit a draft for review that isn't on the same day as the day they fill it out").
	// Empty means the day it is submitted, which is what a visit filled in on the day wants.
	// The server applies the Log a visit rule (checked_visit_date) and drops clock times
	// from another day, which only the kiosk autofill can have put there.
	render_visit_date() {
		const today = frappe.datetime.get_today();
		const earliest = frappe.datetime.add_days(today, -VZ_MAX_BACKFILL_DAYS);
		const scheduled = this.doc.scheduled_visit_date;
		const hint =
			!this.doc.visit_date && scheduled && scheduled < today
				? __("This visit was scheduled for {0}. If you did the work then, pick that day.", [
						frappe.datetime.str_to_user(scheduled),
					])
				: __("Leave it as today unless you are filling in for an earlier day.");
		const $card = $(`
			<div class="vz-card">
				<div class="vz-card-title">${__("Date of visit")}</div>
				<div class="vz-card-sub">${frappe.utils.escape_html(hint)}</div>
				<input class="vz-date" type="date" min="${earliest}" max="${today}">
			</div>
		`);
		const $input = $card.find("input").val(this.doc.visit_date || today);
		if (this.doc.docstatus !== 0) $input.prop("disabled", true);
		$input.on("change", () => {
			const value = $input.val();
			if (value && (value > today || value < earliest)) {
				frappe.msgprint(
					__("Pick a day between {0} and today.", [frappe.datetime.str_to_user(earliest)])
				);
				$input.val(this.doc.visit_date || today);
				return;
			}
			this.set_field("visit_date", value || null);
		});
		this.$wrap.append($card);
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

	// One signature pad, for `field` (client_sign_off, or technician_signature since v1.610.0):
	// each pad's state is its own (this._pads[field]), so the two never share a drawing.
	//
	// The canvas is rebuilt every time Wrap-up renders — leaving the step and
	// coming back, now also by Back and Forward. A signature drawn this visit is
	// kept as a data URL (on pointerup) and drawn back into the new canvas;
	// before, the flag outlived the drawing, and Finish sent a blank image as
	// the client's signature.
	setup_signature($card, field) {
		const canvas = $card.find("canvas")[0];
		const pad = (this._pads[field] = this._pads[field] || { drawn: false, data: null, canvas: null });
		const resize = () => {
			const data = pad.drawn ? pad.data || canvas.toDataURL() : null;
			canvas.width = canvas.offsetWidth;
			canvas.height = canvas.offsetHeight;
			if (data) {
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
			pad.drawn = true;
		});
		const lift = () => {
			if (drawing && pad.drawn) {
				pad.data = canvas.toDataURL("image/png");
				$card.removeClass("vz-sig-missing").find(".vz-sig-needed").hide();
			}
			drawing = false;
		};
		canvas.addEventListener("pointerup", lift);
		canvas.addEventListener("pointercancel", lift);
		$card.find(".vz-link-btn").on("click", () => {
			context.clearRect(0, 0, canvas.width, canvas.height);
			pad.drawn = false;
			pad.data = null;
		});
		pad.canvas = canvas;

		if (this.doc[field] && !pad.drawn) {
			const image = new Image();
			image.onload = () => {
				context.drawImage(image, 0, 0, canvas.width || canvas.offsetWidth, canvas.height || canvas.offsetHeight);
			};
			image.src = this.doc[field];
			pad.drawn = false; // existing signature isn't re-sent
		}
	}

	// Finish without the technician's signature: say so on the pad and bring it into view. The
	// server would refuse the visit for review without it (_finish_on_review).
	ask_technician_signature() {
		const $card = this.$wrap.find(".vz-tech-sig");
		$card.addClass("vz-sig-missing").find(".vz-sig-needed").show();
		const el = $card[0];
		if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
		frappe.show_alert({ message: __("Sign the visit before you finish it."), indicator: "orange" });
	}

	// ----- photos -----------------------------------------------------------

	bind_photo($btn, table, row, field) {
		$btn.on("click", () => {
			const input = document.createElement("input");
			input.type = "file";
			input.accept = "image/*";
			input.capture = "environment";
			input.onchange = () => {
				const file = input.files && input.files[0];
				if (file) this.take_photo(table, row, file, $btn, field);
			};
			input.click();
		});
	}

	// ----- finish -------------------------------------------------------------

	// A last nudge before Finish, never a block: the visit still goes for review, where the
	// email names each task left not done with no reason.
	finish() {
		// The signature first (v1.610.0): a block, unlike the nudge below, so it is asked before
		// the technician has said "Finish anyway".
		if (this.missing_technician_signature()) {
			this.ask_technician_signature();
			return;
		}
		const unexplained = (this.doc.cleaning_tasks || []).filter((row) => vz_task_unexplained(row));
		if (!unexplained.length || this.doc.workflow_state === "Pending Review") return this.finish_now();
		frappe.confirm(
			__(
				"{0} not done, with no reason given. The customer's report will show it as not done. Finish anyway?",
				[unexplained.length === 1 ? __("1 task is") : __("{0} tasks are", [unexplained.length])]
			),
			() => this.finish_now()
		);
	}

	finish_now() {
		const signature = this.pad_value("client_sign_off");
		// The technician's own signature (v1.610.0), sent only by a technician finishing: a
		// reviewer's Approve & Submit never signs as the technician (the server ignores it then
		// too). One already on the visit, from before it was sent back, still counts.
		if (this.missing_technician_signature()) {
			this.ask_technician_signature();
			return;
		}
		const technician_signature = this.technician_finishing() ? this.pad_value("technician_signature") : null;
		// The client's sign-off by email (v1.614.0): an address that cannot be one is said here,
		// with signal or without, rather than discovered after the visit has gone.
		const email_sign_off = this.email_sign_off_value();
		if (email_sign_off !== null && !/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(email_sign_off)) {
			frappe.show_alert({ message: __("Enter the client's email address, or untick Email them a link."), indicator: "orange" });
			const el = this.$wrap.find(".vz-email")[0];
			if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
			return;
		}
		const doc = this.doc;
		this.flush_save()
			.then(() =>
				frappe.call({
					method: "erpnext_enhancements.api.maintenance_visit.finish_visit",
					args: { record: doc.name, signature, technician_signature, email_sign_off, modified: doc.modified },
					freeze: true,
					freeze_message: __("Finishing visit…"),
				})
			)
			.then((r) => {
				const state = (r && r.message) || {};
				const submitted = state.docstatus === 1;
				// Back from the done screen shows the steps again: they must show
				// the visit as it now is (submitted, or pending review), not as
				// an open draft that would autosave into a refusal.
				doc.modified = state.modified || doc.modified;
				if (state.docstatus != null) doc.docstatus = state.docstatus;
				if (state.workflow_state !== undefined) doc.workflow_state = state.workflow_state;
				// A Finish kept on this phone earlier (turned down, or held for a choice) is done.
				if (this._finish[doc.name]) this.drop_finish(doc.name);
				this.say_sign_off_sent(state);
				// Left for another screen while it finished: say so, paint nothing.
				if (doc !== this.doc) {
					frappe.show_alert({
						message: submitted ? __("Visit submitted") : __("Sent for review"),
						indicator: "green",
					});
					return;
				}
				const emailed = state.sign_off
					? `<p class="vz-muted">${__("A link to sign off was emailed to {0}.", [
							frappe.utils.escape_html(state.sign_off.email || ""),
					  ])}</p>`
					: "";
				this.$wrap.empty().append(`
					<div class="vz-done-screen">
						<div class="vz-done-icon">${submitted ? "✅" : "📨"}</div>
						<h4>${submitted ? __("Visit submitted") : __("Sent for review")}</h4>
						<p class="vz-muted">${
							submitted
								? __("Stock, time and billing entries are being generated.")
								: __("A reviewer will approve and submit this visit.")
						}</p>
						${emailed}
					</div>
				`);
				// A new entry for the list, so Back from it returns to this visit
				// (read-only now) rather than skipping the whole visit.
				$(`<button type="button" class="vz-pick-card" style="text-align:center;font-weight:600;">${__("Back to Today's Visits")}</button>`)
					.on("click", () => this.push_route({ record: "", step: "", feature: "" }))
					.appendTo(this.$wrap.find(".vz-done-screen"));
			})
			.catch((error) => {
				// A refused finish showed the server's own message (a required item, the
				// visit out of date) and stays as it was. No answer at all is no signal:
				// the Finish is kept on this phone and sent when there is (v1.596.0).
				if (error && vz_try_later(error)) this.keep_finish(doc, signature, technician_signature, email_sign_off);
			});
	}
}
