#!/usr/bin/env node
/**
 * "Create Project in PM System" shows its progress and refreshes the page (v1.749.0), executed
 * against a stubbed frappe rather than grepped. Nik, 2026-10-27: "create a progress bar to show
 * it's current progress in creation and auto refresh the page when it's done so the new fields
 * show up."
 *
 * Runs the real crm_enhancements/create_project_prompt.js and opportunity.js:
 *
 *   1. The bar is the job's own count of finished steps: it starts at "waiting for a worker",
 *      moves one step per ee_project_creation_progress event, and ignores other deals' events.
 *   2. Watching starts before the enqueue request goes, so a job that reports (or finishes)
 *      while the request is in flight is heard by the dialog and not by the surfaces.
 *   3. When it is over the dialog closes, the record reloads where it is showing (the Desk form,
 *      and the guided page through sf-record-changed), and an alert links the new Project. A
 *      form with unsaved edits is not reloaded under them; it is asked first.
 *   4. A failure closes the bar and says why (escaped), links the Error Log, and offers Try
 *      again. A job a deploy flushed (the events go quiet, and the server has no job) is said to
 *      have stopped, not waited on forever.
 *   5. Opening the dialog while a creation is under way shows that creation instead of a form
 *      that would start a second one.
 *   6. Closing the dialog only stops the watching; the Desk form's own listener, registered once,
 *      then reports. While the dialog watches, that listener stands aside.
 *   7. A project just made offers the next step, "Build out from a planner template" (TASK-2026-02984):
 *      only to someone the planner lets in (boot.page_info, then the server's planner_template_offer),
 *      never for a project that was already there, and its click is set_route to the new Project's
 *      Gantt with build_out=template (the planner's half is in scripts/test_planner_url_filters.js).
 *
 * Every name here is invented. Run: node scripts/test_project_creation_progress.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const JS = path.join(HERE, "..", "erpnext_enhancements", "public", "js", "crm_enhancements");
const PROMPT_JS = path.join(JS, "create_project_prompt.js");
const OPPORTUNITY_JS = path.join(JS, "opportunity.js");

const OPP = "CRM-OPP-2099-00001";
const STATE_METHOD = "erpnext_enhancements.crm_enhancements.project_creation_progress.project_creation_state";
const ENQUEUE = "erpnext_enhancements.crm_enhancements.api.enqueue_project_creation";
const STEPS = [
	{ key: "check", label: "Checking the hand-off" },
	{ key: "project", label: "Creating the project and its tasks" },
	{ key: "attachments", label: "Copying the opportunity's attachments" },
	{ key: "link", label: "Linking the project to this opportunity" },
	{ key: "drive", label: "Creating the Google Drive folder" },
];

let passed = 0;
async function check(label, fn) {
	try {
		await fn();
		passed += 1;
	} catch (e) {
		console.error(`FAIL ${label}`);
		throw e;
	}
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 6; i += 1) await flush();
}

// ---- the stubbed frappe -----------------------------------------------------------------------

function make_world() {
	const w = {
		dialogs: [],
		alerts: [],
		msgprints: [],
		triggers: [],
		routes: [],
		xcalls: [],
		calls: [],
		listeners: {},
		timers: [],
		hidden_msgprints: 0,
		route: ["Form", "Opportunity", OPP],
		answers: {},
		http: {},
		form_handlers: {},
		confirms: [],
		can_write: true,
	};

	class Dialog {
		constructor(opts) {
			Object.assign(this, opts);
			this.shown = false;
			this.html = "";
			this.fields_dict = {};
			for (const f of opts.fields || []) {
				if (f.fieldname) this.fields_dict[f.fieldname] = { $wrapper: { html: (s) => (this.html = s) } };
			}
			const self = this;
			this.btn = {
				prop() {
					return this;
				},
				html(v) {
					if (v === undefined) return "Create Project";
					self.btn_label = v;
					return this;
				},
			};
			w.dialogs.push(this);
		}
		show() {
			this.shown = true;
		}
		hide() {
			if (!this.shown) return; // bootstrap fires no hide event for a modal that is not showing
			this.shown = false;
			if (this.onhide) this.onhide();
		}
		get_primary_btn() {
			return this.btn;
		}
		set_message(m) {
			this.message = m;
		}
		clear_message() {
			this.message = null;
		}
	}

	const escape_html = (s) =>
		String(s == null ? "" : s)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");

	const sandbox = {
		console,
		setTimeout: (fn, ms) => {
			const id = w.timers.length + 1;
			w.timers.push({ id, fn, ms, done: false });
			return id;
		},
		clearTimeout: (id) => {
			const t = w.timers.find((x) => x.id === id);
			if (t) t.done = true;
		},
		__: (txt, args) => String(txt).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] !== undefined ? args[i] : m)),
		$: (target) => ({
			trigger: (event, args) => w.triggers.push({ target, event, args }),
		}),
		document: { nodeName: "#document" },
		cur_frm: null,
		cur_list: null,
	};
	sandbox.window = sandbox;
	const frappe = {
		provide(ns) {
			let obj = sandbox;
			for (const part of ns.split(".")) obj = obj[part] = obj[part] || {};
			return obj;
		},
		xcall(method, args) {
			w.xcalls.push({ method, args });
			const answer = w.answers[method];
			if (answer === undefined) return Promise.reject(new Error(`no answer for ${method}`));
			return Promise.resolve().then(() => (typeof answer === "function" ? answer(args) : answer));
		},
		// The jqXHR frappe.call returns, as a promise the scenario settles: respond() or fail().
		call(opts) {
			let resolve, reject;
			const promise = new Promise((yes, no) => {
				resolve = yes;
				reject = no;
			});
			w.calls.push(
				Object.assign(opts, {
					async respond(r) {
						resolve(r);
						await settle();
					},
					async fail() {
						reject(new Error("HTTP 417"));
						await settle();
					},
				})
			);
			return promise;
		},
		ui: { Dialog, form: { on: (doctype, handlers) => (w.form_handlers[doctype] = handlers) } },
		realtime: {
			on(event, fn) {
				(w.listeners[event] = w.listeners[event] || []).push(fn);
			},
			off(event, fn) {
				w.listeners[event] = (w.listeners[event] || []).filter((f) => f !== fn);
			},
		},
		show_alert: (opts, seconds, actions) => w.alerts.push(Object.assign({}, opts, { seconds, actions })),
		set_route: (...args) => w.routes.push(args),
		msgprint: (opts) => w.msgprints.push(opts),
		hide_msgprint: () => (w.hidden_msgprints += 1),
		confirm: (message) => {
			w.confirms.push(message);
			return null;
		},
		perm: { has_perm: (doctype, level, ptype, doc) => (ptype === "write" ? w.can_write : true) },
		get_route: () => w.route,
		utils: {
			escape_html,
			split_emails: (s) =>
				String(s || "")
					.split(",")
					.map((x) => x.trim())
					.filter(Boolean),
			validate_type: (v) => String(v).includes("@"),
		},
		views: { formview: {} },
		boot: {},
	};
	sandbox.fetch = (url, init) => {
		const method = String(url).replace("/api/method/", "");
		const args = JSON.parse((init && init.body) || "{}");
		w.xcalls.push({ method, args, quiet: true });
		if (w.http[method]) return Promise.resolve({ ok: false, status: w.http[method], json: () => Promise.resolve({}) });
		const answer = w.answers[method];
		if (answer === undefined) return Promise.reject(new TypeError(`no answer for ${method}`));
		return Promise.resolve()
			.then(() => (typeof answer === "function" ? answer(args) : answer))
			.then((message) => ({ ok: true, status: 200, json: () => Promise.resolve({ message }) }));
	};
	sandbox.AbortController = AbortController;
	sandbox.frappe = frappe;
	w.sandbox = sandbox;
	w.frappe = frappe;

	w.emit = (event, data) => (w.listeners[event] || []).slice().forEach((fn) => fn(data));
	w.run_timers = (ms) => {
		w.timers.filter((t) => !t.done && t.ms <= ms).forEach((t) => {
			t.done = true;
			t.fn();
		});
	};
	w.pending_timers = () => w.timers.filter((t) => !t.done).length;
	w.crm = () => sandbox.erpnext_enhancements.crm;
	w.dialog = (title) => w.dialogs.filter((d) => d.title === title).pop();
	w.frm = (overrides) => ({
		doctype: "Opportunity",
		doc: { name: OPP },
		reloads: 0,
		dirty: false,
		is_dirty() {
			return this.dirty;
		},
		reload_doc() {
			this.reloads += 1;
		},
		...overrides,
	});

	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(PROMPT_JS, "utf8"), sandbox, { filename: PROMPT_JS });
	return w;
}

// Answers for opening the create dialog on a deal with nothing under way and its gate open.
function idle_answers(w) {
	Object.assign(w.answers, {
		[STATE_METHOD]: { phase: "idle", project: null, steps: STEPS },
		"erpnext_enhancements.crm_enhancements.handoff.handoff_state": { available: true, enabled: true, gate_open: true },
		"erpnext_enhancements.crm_enhancements.project_prompt.default_project_notify_users": ["office@example.com"],
		"frappe.client.get_list": [],
		"erpnext_enhancements.project_enhancements.fountain_move_jobs.opportunity_defaults": {},
	});
}

// Through the Create Project dialog to the enqueue request (not yet answered).
async function start(w, frm) {
	idle_answers(w);
	w.crm().open_create_project_dialog(OPP, { frm });
	await settle();
	const form = w.dialog("Create Project");
	assert.ok(form, "the Create Project dialog opens");
	form.primary_action({ project_template: "Fountain Build", users_to_notify: "office@example.com" });
	const request = w.calls.pop();
	assert.equal(request.method, ENQUEUE);
	return request;
}

const queued = { status: "queued", phase: "queued", state: { phase: "queued", step: null, done: 0 }, steps: STEPS };
const bar_width = (html) => Number(/width: (\d+)%/.exec(html)[1]);

// ---- 1-3: the bar, and the refresh ------------------------------------------------------------

await check("the bar counts the job's finished steps and the page refreshes", async () => {
	const w = make_world();
	const frm = w.frm();
	const request = await start(w, frm);
	assert.equal(w.crm().is_tracking_project_creation(OPP), true, "watching starts before the request goes");
	await request.respond({ message: queued });
	const bar = w.dialog("Creating Project");
	assert.ok(bar && bar.shown, "the progress dialog shows");
	assert.equal(w.dialog("Create Project").shown, false, "the form dialog is gone");
	assert.match(bar.html, /Waiting for a background worker/);
	assert.equal(bar_width(bar.html), 0);
	assert.equal((bar.html.match(/<li class="ee-pc-step pending">/g) || []).length, 5);

	w.emit("ee_project_creation_progress", { opportunity: OPP, phase: "running", step: "project", done: 1 });
	assert.match(bar.html, /Step 2 of 5: Creating the project and its tasks/);
	assert.equal(bar_width(bar.html), 20);
	assert.match(bar.html, /<li class="ee-pc-step done">/);
	assert.match(bar.html, /<li class="ee-pc-step current">/);

	const before = bar.html;
	w.emit("ee_project_creation_progress", { opportunity: "CRM-OPP-2099-00002", phase: "running", step: "drive", done: 4 });
	assert.equal(bar.html, before, "another deal's progress is not drawn here");

	w.emit("ee_project_creation_progress", { opportunity: OPP, phase: "running", step: "drive", done: 4 });
	assert.equal(bar_width(bar.html), 80);

	w.emit("project_creation_status", {
		opportunity_name: OPP,
		status: "success",
		project: "PRJ-0001",
		project_doc: { name: "PRJ-0001" },
		drive_success: true,
	});
	assert.equal(bar.shown, false, "the bar closes");
	await settle(); // the alert waits for the Build out offer's answer
	assert.equal(frm.reloads, 1, "the Desk form reloads once");
	assert.deepEqual(
		w.triggers.map((t) => [t.event, ...t.args]),
		[["sf-record-changed", "Opportunity", OPP]],
		"the guided page is told to reload"
	);
	assert.equal(w.alerts.length, 1);
	assert.match(w.alerts[0].message, /href="\/desk\/project\/PRJ-0001"/);
	assert.equal(w.alerts[0].indicator, "green");
	assert.equal(w.msgprints.length, 0);
	assert.equal(w.crm().is_tracking_project_creation(OPP), false);
	assert.equal((w.listeners.ee_project_creation_progress || []).length, 0, "its listeners are removed");
	assert.equal(w.pending_timers(), 0, "and its timer");
});

await check("a form with unsaved edits is asked, not reloaded under them", async () => {
	const w = make_world();
	const frm = w.frm({ dirty: true });
	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	assert.equal(frm.reloads, 0);
	const ask = w.msgprints.pop();
	assert.equal(ask.title, "Reload to see the project");
	ask.primary_action.action();
	assert.equal(frm.reloads, 1);
	assert.equal(w.hidden_msgprints, 1);
});

await check("a dirty form that is not on screen is left alone", async () => {
	const w = make_world();
	const frm = w.frm({ dirty: true });
	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.route = ["guided-entry", "Opportunity", OPP];
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	assert.equal(frm.reloads, 0);
	assert.equal(w.msgprints.length, 0);
	assert.equal(w.triggers.length, 1, "the guided page still reloads itself");
});

await check("a job that reports before the request returns is heard by the dialog", async () => {
	const w = make_world();
	const frm = w.frm();
	const request = await start(w, frm);
	w.emit("ee_project_creation_progress", { opportunity: OPP, phase: "running", step: "check", done: 0 });
	await request.respond({ message: queued });
	const bar = w.dialog("Creating Project");
	assert.match(bar.html, /Step 1 of 5: Checking the hand-off/, "the reply's older 'queued' does not overwrite it");
});

await check("a job that finishes before the request returns is reported once, with no dialog", async () => {
	const w = make_world();
	const frm = w.frm();
	const request = await start(w, frm);
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	await request.respond({ message: queued });
	assert.equal(w.dialog("Creating Project"), undefined);
	await settle();
	assert.equal(w.alerts.length, 1);
	assert.equal(frm.reloads, 1);
});

await check("a refusal of the request hands the form back and stops watching", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.fail();
	assert.equal(w.crm().is_tracking_project_creation(OPP), false);
	assert.equal(w.dialog("Create Project").message, null, "the form's fields come back");
});

// ---- 4: failure, and a job a deploy flushed ---------------------------------------------------

await check("a failure closes the bar, says why (escaped), links the Error Log and offers Try again", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: queued });
	w.emit("project_creation_status", {
		opportunity_name: OPP,
		status: "failed",
		reason: 'Template <img src=x onerror="alert(1)"> is broken',
		failed_step: "project",
		error_log: "ERR-0001",
	});
	assert.equal(w.dialog("Creating Project").shown, false);
	const said = w.msgprints.pop();
	assert.equal(said.title, "Project creation failed");
	assert.ok(!said.message.includes("<img"), "the reason is escaped");
	assert.match(said.message, /Template &lt;img/);
	assert.match(said.message, /href="\/desk\/error-log\/ERR-0001"/);
	assert.equal(said.primary_action.label, "Try again");
	const asked = w.xcalls.length;
	said.primary_action.action();
	await settle();
	assert.equal(w.xcalls[asked].method, STATE_METHOD, "Try again asks where things stand first");
	assert.ok(w.dialogs.filter((d) => d.title === "Create Project").length >= 2, "and opens the form again");
});

await check("a job a deploy flushed is said to have stopped, not waited on forever", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: queued });
	w.answers[STATE_METHOD] = { phase: "running", project: null, state: { phase: "running", step: "attachments", done: 2 }, steps: STEPS };
	w.run_timers(15000);
	await settle();
	const bar = w.dialog("Creating Project");
	assert.equal(bar.shown, true, "still running: keep watching");
	assert.match(bar.html, /Step 3 of 5/);
	w.answers[STATE_METHOD] = { phase: "idle", project: null, state: null, steps: STEPS };
	w.run_timers(15000);
	await settle();
	assert.equal(bar.shown, false);
	const said = w.msgprints.pop();
	assert.equal(said.title, "Project creation stopped");
	assert.match(said.message, /safe to start again/);
	assert.equal(said.primary_action.label, "Try again");
});

await check("a quiet job that did finish is reported from the recorded outcome", async () => {
	const w = make_world();
	const frm = w.frm();
	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.answers[STATE_METHOD] = {
		phase: "finished",
		project: "PRJ-0001",
		state: { phase: "finished", result: { status: "success", project: "PRJ-0001", drive_error: true } },
		steps: STEPS,
	};
	w.run_timers(15000);
	await settle();
	assert.equal(frm.reloads, 1);
	assert.match(w.alerts[0].message, /PRJ-0001/);
	assert.equal(w.msgprints.pop().title, "Google Drive Integration Failed");
});

await check("a blocked hand-off says what to do", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: queued });
	w.emit("project_creation_status", { opportunity_name: OPP, status: "blocked", blocked_reason: "Book the <b>meeting</b> first." });
	const said = w.msgprints.pop();
	assert.equal(said.title, "Hand-Off Required");
	assert.match(said.message, /&lt;b&gt;meeting/);
});

await check("a project that already pointed here is reported as linked, not made", async () => {
	const w = make_world();
	const frm = w.frm();
	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.emit("project_creation_status", { opportunity_name: OPP, status: "exists", project: "PRJ-0003", adopted: true });
	assert.match(w.alerts[0].message, /Linked project .*PRJ-0003.*no second project/);
	assert.equal(frm.reloads, 1);
});

// ---- 5: one creation at a time ----------------------------------------------------------------

await check("opening the dialog during a creation shows that creation", async () => {
	const w = make_world();
	idle_answers(w);
	w.answers[STATE_METHOD] = { phase: "running", project: null, state: { phase: "running", step: "link", done: 3 }, steps: STEPS };
	w.crm().open_create_project_dialog(OPP, { frm: w.frm() });
	await settle();
	assert.equal(w.dialog("Create Project"), undefined, "no form to start a second one");
	const bar = w.dialog("Creating Project");
	assert.match(bar.html, /Step 4 of 5: Linking the project/);
	assert.equal(w.calls.length, 0, "nothing is queued");
});

await check("a second click while watching brings the same dialog back", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: queued });
	const asked = w.xcalls.length;
	w.crm().open_create_project_dialog(OPP, {});
	assert.equal(w.xcalls.length, asked);
	assert.equal(w.dialogs.filter((d) => d.title === "Creating Project").length, 1);
});

await check("the server's 'already running' reply is watched like a fresh one", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: { ...queued, status: "running", phase: "running", state: { phase: "running", step: "drive", done: 4 } } });
	assert.match(w.dialog("Creating Project").html, /Step 5 of 5/);
});

await check("labels from the server are escaped in the step list", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: { ...queued, steps: [{ key: "check", label: "<script>x</script>" }] } });
	const html = w.dialog("Creating Project").html;
	assert.ok(!html.includes("<script>x"), "no markup from a label");
	assert.match(html, /&lt;script&gt;x/);
});

// ---- 6: closing early, and the Desk form's own listener --------------------------------------

await check("closing the dialog stops watching; the form's listener, registered once, reports", async () => {
	const w = make_world();
	const frm = w.frm();
	// opportunity.js is a form script: evaluated once per form load. Twice here, as a reload would.
	vm.runInContext(fs.readFileSync(OPPORTUNITY_JS, "utf8"), w.sandbox, { filename: OPPORTUNITY_JS });
	vm.runInContext(fs.readFileSync(OPPORTUNITY_JS, "utf8"), w.sandbox, { filename: OPPORTUNITY_JS });
	w.frappe.views.formview.Opportunity = { frm };
	assert.equal((w.listeners.project_creation_status || []).length, 1, "one listener, however often the script runs");

	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.emit("ee_project_creation_progress", { opportunity: OPP, phase: "running", step: "project", done: 1 });
	// While the dialog watches, the form's listener stands aside: one alert and one reload.
	const bar = w.dialog("Creating Project");
	bar.hide();
	assert.equal(w.crm().is_tracking_project_creation(OPP), false);
	assert.match(w.alerts.pop().message, /still being created/);
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	assert.equal(frm.reloads, 1, "the form's listener reloads it");
	await settle();
	assert.equal(w.alerts.length, 1);
	assert.equal(w.triggers.length, 0, "form_only: the guided page has its own listener");
});

await check("while the dialog watches, the form's listener stays quiet", async () => {
	const w = make_world();
	const frm = w.frm();
	vm.runInContext(fs.readFileSync(OPPORTUNITY_JS, "utf8"), w.sandbox, { filename: OPPORTUNITY_JS });
	w.frappe.views.formview.Opportunity = { frm };
	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	assert.equal(frm.reloads, 1, "reloaded once, by the dialog");
	await settle();
	assert.equal(w.alerts.length, 1, "said once");
});

// ---- 7: the next step, Build out from a planner template (TASK-2026-02984) --------------------

const OFFER = "erpnext_enhancements.api.planner_templates.planner_template_offer";

// A made project, with the planner's page among this person's (boot.page_info) unless told not.
async function made(w, { planner = true } = {}) {
	if (planner) w.frappe.boot.page_info = { "project-planner": {} };
	const frm = w.frm();
	const request = await start(w, frm);
	await request.respond({ message: queued });
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	await settle();
	return frm;
}

await check("the success message offers Build out from a planner template, which opens the planner on it", async () => {
	const w = make_world();
	w.answers[OFFER] = { offer: true };
	await made(w);
	const said = w.alerts.pop();
	assert.match(said.message, /PRJ-0001/, "still the success message");
	assert.match(said.message, /data-action="ee_build_out_from_template">Build out from a planner template<\/a>/);
	assert.ok(said.seconds >= 15, "long enough to reach for");
	assert.equal(typeof said.actions.ee_build_out_from_template, "function", "show_alert binds the link to it");
	let prevented = false;
	said.actions.ee_build_out_from_template({ preventDefault: () => (prevented = true) });
	assert.equal(prevented, true, "the href=# is not followed");
	// As JSON: the options object was made in the script's own realm, with its own Object prototype.
	assert.equal(
		JSON.stringify(w.routes.pop()),
		JSON.stringify(["project-planner", "gantt", "PRJ-0001", { project: "PRJ-0001", build_out: "template" }])
	);
});

await check("no link for someone the planner does not let in, and the server is not asked", async () => {
	const w = make_world();
	w.answers[OFFER] = { offer: true };
	await made(w, { planner: false });
	assert.ok(!w.alerts.pop().message.includes("data-action"));
	assert.equal(w.xcalls.filter((c) => c.method === OFFER).length, 0);
});

await check("no link when the server says no (no template, no Task write), or cannot be asked", async () => {
	for (const answer of [{ offer: false }, () => Promise.reject(new Error("down"))]) {
		const w = make_world();
		w.answers[OFFER] = answer;
		await made(w);
		const said = w.alerts.pop();
		assert.match(said.message, /PRJ-0001/, "the plain message still comes");
		assert.ok(!said.message.includes("data-action"));
		assert.equal(said.actions, undefined);
	}
});

await check("a project that was already there offers no next step", async () => {
	const w = make_world();
	w.frappe.boot.page_info = { "project-planner": {} };
	w.answers[OFFER] = { offer: true };
	const request = await start(w, w.frm());
	await request.respond({ message: queued });
	w.emit("project_creation_status", { opportunity_name: OPP, status: "exists", project: "PRJ-0003" });
	await settle();
	assert.ok(!w.alerts.pop().message.includes("data-action"));
	assert.equal(w.xcalls.filter((c) => c.method === OFFER).length, 0);
});

await check("the form's listener ignores another deal's creation", async () => {
	const w = make_world();
	const frm = w.frm();
	vm.runInContext(fs.readFileSync(OPPORTUNITY_JS, "utf8"), w.sandbox, { filename: OPPORTUNITY_JS });
	w.frappe.views.formview.Opportunity = { frm };
	w.emit("project_creation_status", { opportunity_name: "CRM-OPP-2099-00002", status: "success", project: "PRJ-0009" });
	assert.equal(frm.reloads, 0);
	assert.equal(w.alerts.length, 0);
});

// ---- 8: the review's hardening (v1.749.0) -----------------------------------------------------

await check("a request that fails without any callback (a QueryTimeout) leaves nothing watching", async () => {
	// v16 runs neither callback nor error on a QueryTimeoutError; only the jqXHR settles.
	const w = make_world();
	const request = await start(w, w.frm());
	await request.fail();
	assert.equal(w.crm().is_tracking_project_creation(OPP), false);
	const asked = w.xcalls.length;
	w.crm().open_create_project_dialog(OPP, {});
	await settle();
	assert.equal(w.xcalls[asked].method, STATE_METHOD, "the next click is not swallowed");
	assert.equal(w.dialogs.filter((d) => d.title === "Create Project").length, 2);
});

await check("the state reads are quiet: a server away for a deploy is one calm 'reconnecting' line", async () => {
	const w = make_world();
	const request = await start(w, w.frm());
	await request.respond({ message: queued });
	const bar = w.dialog("Creating Project");
	w.http[STATE_METHOD] = 502;
	w.run_timers(15000);
	await settle();
	assert.equal(w.msgprints.length, 0, "no popup for a 502");
	assert.match(bar.html, /Reconnecting to the server/);
	assert.equal(bar.shown, true, "still watching");
	assert.equal(w.pending_timers() > 0, true, "and it asks again");
	w.run_timers(15000);
	await settle();
	assert.equal(w.msgprints.length, 0, "nor one per poll");
	delete w.http[STATE_METHOD];
	w.answers[STATE_METHOD] = { phase: "running", project: null, state: { phase: "running", step: "link", done: 3 }, steps: STEPS };
	w.run_timers(15000);
	await settle();
	assert.match(bar.html, /Step 4 of 5/, "back to the steps once it answers");
	assert.ok(!bar.html.includes("Reconnecting"));
	w.emit("ee_project_creation_progress", { opportunity: OPP, phase: "running", step: "drive", done: 4 });
	assert.match(bar.html, /Step 5 of 5/);
});

await check("closed early on the Kanban board, the outcome is still said there, once", async () => {
	const w = make_world();
	let refreshed = 0;
	w.sandbox.cur_list = { refresh: () => (refreshed += 1) };
	const request = await start(w, undefined);
	await request.respond({ message: queued });
	w.route = ["List", "Opportunity", "Kanban", "Sales Pipeline"];
	w.dialog("Creating Project").hide();
	w.alerts.length = 0;
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	await settle();
	assert.equal(w.alerts.length, 1, "said on the board");
	assert.match(w.alerts[0].message, /PRJ-0001/);
	assert.equal(refreshed, 1, "and the board repaints");
	w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
	await settle();
	assert.equal(w.alerts.length, 1, "once");
});

await check("closed early where a surface reports, the fallback stays quiet", async () => {
	for (const where of ["form", "guided"]) {
		const w = make_world();
		if (where === "form") w.frappe.views.formview.Opportunity = { frm: w.frm() };
		const request = await start(w, undefined);
		await request.respond({ message: queued });
		w.route = where === "form" ? ["Form", "Opportunity", OPP] : ["guided-entry", "Opportunity", OPP, "handoff"];
		w.dialog("Creating Project").hide();
		w.alerts.length = 0;
		w.emit("project_creation_status", { opportunity_name: OPP, status: "success", project: "PRJ-0001" });
		await settle();
		assert.equal(w.alerts.length, 0, `${where}: its own listener says it, not this`);
	}
});

await check("the reopen prompt asks only someone who may change the deal", async () => {
	// opportunity.js's real refresh on a won deal with no project: a writer is asked, a reader is not
	// (creating the project needs write on the Opportunity, so a reader would only be refused).
	for (const can_write of [true, false]) {
		const w = make_world();
		w.can_write = can_write;
		w.answers["erpnext_enhancements.crm_enhancements.handoff.handoff_state"] = {
			available: true,
			enabled: true,
			gate_applies: true,
			held: false,
			event: "EV-0001",
		};
		vm.runInContext(fs.readFileSync(OPPORTUNITY_JS, "utf8"), w.sandbox, { filename: OPPORTUNITY_JS });
		const handlers = w.form_handlers.Opportunity;
		const frm = Object.assign(w.frm(), {
			doc: { name: OPP, status: "Closed Won", custom_created_project: "", custom_value_stream: [] },
			is_new: () => false,
			remove_custom_button: () => {},
			add_custom_button: () => {},
			tags: null,
		});
		handlers.onload(frm);
		handlers.refresh(frm);
		await settle();
		assert.equal(w.confirms.length, can_write ? 1 : 0, can_write ? "a writer is asked" : "a reader is not");
		if (can_write) assert.match(w.confirms[0], /Create project now\?/);
	}
});

console.log(`create project progress: ${passed} checks passed`);
