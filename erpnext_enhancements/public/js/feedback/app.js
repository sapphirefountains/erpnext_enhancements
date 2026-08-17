/**
 * The feedback SPA. One class, plain DOM, no framework.
 *
 * State is a plain object and rendering is "clear the pane and rebuild it". At this size that
 * is not a compromise — the largest thing on screen is a twelve-row proposal — and it removes
 * the whole class of bug where a diffed view and the server disagree about what is on screen.
 * `/chat` uses the same approach for the same reason.
 *
 * Pure logic lives elsewhere on purpose, so it can be tested by a plain node script:
 * `routes.js` (URL <-> view) and `context.js` (what the requester was looking at).
 */

import { call, upload, M, FeedbackCallError } from "./transport.js";
import {
	VIEW_NEW,
	VIEW_MINE,
	VIEW_REVIEW,
	VIEW_REQUEST,
	parseRoute,
	buildRoute,
	defaultView,
} from "./routes.js";
import { captureContext } from "./context.js";
import {
	append,
	button,
	checkbox,
	clear,
	deskLink,
	el,
	field,
	input,
	link,
	relativeTime,
	richText,
	select,
	statusPill,
	textarea,
} from "./dom.js";

const PRIORITIES = ["Low", "Medium", "High", "Urgent"];

export class FeedbackApp {
	constructor(root, boot) {
		this.root = root;
		this.boot = boot || {};
		this.state = {
			user: this.boot.user || "",
			fullName: this.boot.full_name || "",
			isReviewer: false,
			paused: false,
			requestTypes: ["Feature", "Bug"],
			impacts: [],
			myRequests: [],
			reviewQueue: [],
			view: VIEW_NEW,
			name: "",
			detail: null,
			busy: false,
		};
		// Captured once, at load, from the page they came *from*. Reading it later would
		// capture this page instead.
		this.captured = captureContext({
			referrer: document.referrer,
			origin: window.location.origin,
			userAgent: navigator.userAgent,
			build: this.boot.build,
		});
	}

	async mount() {
		this.buildLayout();
		window.addEventListener("popstate", () => this.routeTo(window.location.pathname));
		try {
			const data = await call(M.BOOTSTRAP);
			this.applyBootstrap(data);
		} catch (e) {
			this.fatal(e);
			return;
		}
		this.routeTo(window.location.pathname, false);
	}

	applyBootstrap(data) {
		const payload = data || {};
		this.state.isReviewer = !!payload.is_reviewer;
		this.state.paused = !!payload.paused;
		this.state.requestTypes = payload.request_types || this.state.requestTypes;
		this.state.impacts = payload.impacts || [];
		this.state.myRequests = payload.my_requests || [];
		this.state.reviewQueue = payload.review_queue || [];
		if (payload.full_name) this.state.fullName = payload.full_name;
		this.renderNav();
	}

	// ------------------------------------------------------------------ layout

	buildLayout() {
		clear(this.root);
		this.header = el("header", "ee-fb-header");
		this.nav = el("nav", "ee-fb-nav");
		this.banner = el("div", "ee-fb-banner");
		this.banner.setAttribute("role", "status");
		this.banner.hidden = true;
		this.pane = el("main", "ee-fb-pane");

		append(
			this.header,
			el("h1", "ee-fb-title", "Feedback"),
			el("div", "ee-fb-who", this.state.fullName || this.state.user)
		);
		append(this.root, this.header, this.nav, this.banner, this.pane);
	}

	renderNav() {
		clear(this.nav);
		const tabs = [
			{ view: VIEW_NEW, label: "New request" },
			{ view: VIEW_MINE, label: `My requests${this.state.myRequests.length ? ` (${this.state.myRequests.length})` : ""}` },
		];
		if (this.state.isReviewer) {
			tabs.push({
				view: VIEW_REVIEW,
				label: `Review queue${this.state.reviewQueue.length ? ` (${this.state.reviewQueue.length})` : ""}`,
			});
		}
		for (const tab of tabs) {
			const node = link(tab.label, buildRoute(tab.view), (href) => this.navigate(href));
			node.className = `ee-fb-tab${this.state.view === tab.view ? " ee-fb-tab-active" : ""}`;
			if (this.state.view === tab.view) node.setAttribute("aria-current", "page");
			this.nav.appendChild(node);
		}
	}

	// ------------------------------------------------------------------ routing

	navigate(href) {
		window.history.pushState({}, "", href);
		this.routeTo(href);
	}

	routeTo(pathname) {
		const route = parseRoute(String(pathname).split("?")[0]);
		// The bare route resolves to whichever view is useful for this person, and the URL is
		// corrected so a refresh lands in the same place.
		if (route.view === VIEW_NEW && String(pathname).replace(/\/+$/, "") === "/feedback") {
			const preferred = defaultView(this.state.isReviewer, this.state.myRequests.length > 0);
			if (preferred !== VIEW_NEW) {
				window.history.replaceState({}, "", buildRoute(preferred));
				route.view = preferred;
			}
		}
		this.state.view = route.view;
		this.state.name = route.name;
		this.renderNav();
		this.clearBanner();

		if (route.view === VIEW_REQUEST) return this.renderRequest(route.name);
		if (route.view === VIEW_MINE) return this.renderMine();
		if (route.view === VIEW_REVIEW) return this.renderReview();
		return this.renderNew();
	}

	// ------------------------------------------------------------------ new request

	renderNew() {
		clear(this.pane);
		if (this.state.paused) {
			append(
				this.pane,
				this.notice(
					"New requests are paused",
					"Somebody has turned intake off for the moment. Your existing requests are still here."
				)
			);
			return;
		}

		const form = el("form", "ee-fb-form");
		form.addEventListener("submit", (ev) => ev.preventDefault());

		const typeInput = select(this.state.requestTypes, "Bug");
		const titleInput = input("text", "One line: what is wrong, or what you want", "");
		titleInput.maxLength = 200;
		const impactInput = select(this.state.impacts, this.state.impacts[1] || this.state.impacts[0]);
		const descInput = textarea("What happened, and what you expected instead.", "", 7);
		const stepsInput = textarea("1. Open …\n2. Click …\n3. It does …", "", 5);

		const stepsField = field(
			"Steps to reproduce",
			stepsInput,
			"Bugs only. Worth the two minutes: without these the first reply is always a question."
		);
		const syncSteps = () => {
			stepsField.row.hidden = typeInput.value !== "Bug";
		};
		typeInput.addEventListener("change", syncSteps);
		syncSteps();

		const attachments = this.buildAttachmentPicker();

		const submit = button("Submit", "ee-fb-btn ee-fb-btn-primary", async () => {
			await this.submitRequest({
				submit,
				values: {
					request_type: typeInput.value,
					title: titleInput.value,
					impact: impactInput.value,
					description: descInput.value,
					steps_to_reproduce: typeInput.value === "Bug" ? stepsInput.value : "",
					...this.captured,
				},
				attachments,
			});
		});

		append(
			form,
			field("Type", typeInput).row,
			field("Title", titleInput).row,
			field("Impact", impactInput, "Your read on it. It informs triage; it does not set the priority of the work.").row,
			field("Description", descInput).row,
			stepsField.row,
			attachments.row,
			this.contextSummary(),
			append(el("div", "ee-fb-actions"), submit)
		);
		append(this.pane, form);
		titleInput.focus();
	}

	contextSummary() {
		const parts = [];
		if (this.captured.context_doctype) {
			parts.push(
				`${this.captured.context_doctype}${this.captured.context_docname ? ` ${this.captured.context_docname}` : ""}`
			);
		} else if (this.captured.context_url) {
			parts.push(this.captured.context_url);
		}
		if (this.captured.context_app_version) parts.push(`build ${this.captured.context_app_version}`);
		if (!parts.length) return null;

		const node = el("p", "ee-fb-context");
		append(
			node,
			el("span", "ee-fb-context-label", "Attached automatically: "),
			el("span", null, parts.join(" · "))
		);
		return node;
	}

	buildAttachmentPicker() {
		const picker = document.createElement("input");
		picker.type = "file";
		picker.accept = "image/*,.pdf,.txt,.log";
		picker.multiple = true;
		picker.className = "ee-fb-input";

		const list = el("div", "ee-fb-attachments");
		const uploaded = [];

		picker.addEventListener("change", async () => {
			const files = Array.from(picker.files || []);
			picker.value = "";
			for (const file of files) {
				if (uploaded.length >= 5) break;
				const row = el("div", "ee-fb-attachment", `${file.name} — uploading…`);
				list.appendChild(row);
				try {
					// Upload first, link on submit. A file uploaded against nothing is harmless;
					// a request pointing at a file that failed to upload is not.
					const result = await upload(file, (fraction) => {
						row.textContent = `${file.name} — ${Math.round(fraction * 100)}%`;
					}).promise;
					uploaded.push(result.name);
					row.textContent = `${file.name} — ready`;
					row.classList.add("ee-fb-attachment-ok");
				} catch (e) {
					row.textContent = `${file.name} — ${e.message}`;
					row.classList.add("ee-fb-attachment-bad");
				}
			}
		});

		const wrapper = field("Attachments", picker, "A screenshot answers more than a paragraph.");
		append(wrapper.row, list);
		return { row: wrapper.row, names: uploaded };
	}

	async submitRequest({ submit, values, attachments }) {
		if (this.state.busy) return;
		this.setBusy(submit, true, "Submitting…");
		try {
			const result = await call(M.SUBMIT, { payload: values, attachments: attachments.names });
			const refreshed = await call(M.BOOTSTRAP);
			this.applyBootstrap(refreshed);
			if (result && result.rejected && result.rejected.length) {
				// Reported rather than swallowed — see `api/feedback.py`. A field the server
				// refused is a bug in this file, and a silent one is found weeks later.
				this.showBanner(`Filed, but some fields were not saved: ${result.rejected.join(", ")}`, "warn");
			}
			this.navigate(buildRoute(VIEW_REQUEST, result.name));
		} catch (e) {
			this.showBanner(e.message, "bad");
		} finally {
			this.setBusy(submit, false, "Submit");
		}
	}

	// ------------------------------------------------------------------ lists

	renderMine() {
		clear(this.pane);
		if (!this.state.myRequests.length) {
			append(
				this.pane,
				this.notice("Nothing yet", "Anything you file will show up here with its status.")
			);
			return;
		}
		append(this.pane, this.requestList(this.state.myRequests, { showRequester: false }));
	}

	renderReview() {
		clear(this.pane);
		if (!this.state.isReviewer) {
			append(this.pane, this.notice("Not for you", "Only a System Manager can review requests."));
			return;
		}
		if (!this.state.reviewQueue.length) {
			append(this.pane, this.notice("Queue is empty", "Nothing is waiting on a decision."));
			return;
		}
		append(this.pane, this.requestList(this.state.reviewQueue, { showRequester: true }));
	}

	requestList(rows, { showRequester }) {
		const list = el("ul", "ee-fb-list");
		for (const row of rows) {
			const item = el("li", "ee-fb-list-item");
			const head = el("div", "ee-fb-list-head");
			append(
				head,
				link(row.title || row.name, buildRoute(VIEW_REQUEST, row.name), (href) => this.navigate(href)),
				statusPill(row.status)
			);

			const meta = el("div", "ee-fb-list-meta");
			const bits = [row.request_type, row.impact];
			if (showRequester && row.requested_by) bits.push(row.requested_by);
			bits.push(relativeTime(row.creation));
			append(meta, el("span", null, bits.filter(Boolean).join(" · ")));

			append(item, head, meta);
			if (row.status === "Breakdown Failed" && row.breakdown_error) {
				append(item, el("div", "ee-fb-list-error", row.breakdown_error));
			}
			list.appendChild(item);
		}
		return list;
	}

	// ------------------------------------------------------------------ detail

	async renderRequest(name) {
		clear(this.pane);
		append(this.pane, el("div", "ee-fb-loading", "Loading…"));
		let detail;
		try {
			detail = await call(M.GET, { name });
		} catch (e) {
			clear(this.pane);
			append(this.pane, this.notice("Cannot open that", e.message));
			return;
		}
		this.state.detail = detail;
		clear(this.pane);

		append(
			this.pane,
			this.detailHeader(detail),
			this.detailBody(detail),
			this.detailDecision(detail)
		);

		if (this.state.isReviewer) {
			if (detail.status === "Submitted") append(this.pane, this.triagePanel(detail));
			if (detail.duplicate_candidates.length) append(this.pane, this.duplicatePanel(detail));
			if (detail.status === "Approved") append(this.pane, this.waitingPanel(detail));
			if (detail.status === "Breakdown Failed") append(this.pane, this.failedPanel(detail));
			if (detail.status === "Breakdown Ready") append(this.pane, this.proposalPanel(detail));
		}
		if (detail.proposed_tasks.some((row) => row.created_task)) {
			append(this.pane, this.createdPanel(detail));
		}
	}

	detailHeader(detail) {
		const head = el("header", "ee-fb-detail-head");
		append(head, el("h2", "ee-fb-detail-title", detail.title), statusPill(detail.status));
		const meta = el("div", "ee-fb-list-meta");
		append(
			meta,
			el(
				"span",
				null,
				[detail.name, detail.request_type, detail.impact, detail.requester_name, relativeTime(detail.creation)]
					.filter(Boolean)
					.join(" · ")
			)
		);
		append(head, meta);
		return head;
	}

	detailBody(detail) {
		const body = el("section", "ee-fb-section");
		append(body, el("h3", "ee-fb-section-title", "What was reported"), richText(detail.description));
		if (detail.steps_to_reproduce) {
			append(
				body,
				el("h3", "ee-fb-section-title", "Steps to reproduce"),
				richText(detail.steps_to_reproduce)
			);
		}
		const context = detail.context || {};
		const bits = [
			context.doctype ? `${context.doctype} ${context.docname || ""}`.trim() : context.url,
			context.app_version ? `build ${context.app_version}` : "",
			context.user_agent,
		].filter(Boolean);
		if (bits.length) {
			append(body, el("h3", "ee-fb-section-title", "Captured context"), el("p", "ee-fb-context", bits.join(" · ")));
		}
		if (detail.attachments && detail.attachments.length) {
			const files = el("div", "ee-fb-attachments");
			for (const file of detail.attachments) {
				files.appendChild(deskLink(file.file_name || file.name, file.file_url));
			}
			append(body, el("h3", "ee-fb-section-title", "Attachments"), files);
		}
		return body;
	}

	detailDecision(detail) {
		if (!detail.decided_by) return null;
		const node = el("section", "ee-fb-section");
		append(node, el("h3", "ee-fb-section-title", "Decision"));
		append(node, el("p", null, `${detail.status} by ${detail.decided_by} · ${relativeTime(detail.decided_at)}`));
		if (detail.decision_reason) append(node, el("p", "ee-fb-reason", detail.decision_reason));
		if (detail.duplicate_of_task) {
			append(node, deskLink(`Already covered by ${detail.duplicate_of_task}`, `/app/task/${detail.duplicate_of_task}`));
		}
		return node;
	}

	// ------------------------------------------------------------------ triage

	triagePanel(detail) {
		const panel = el("section", "ee-fb-section ee-fb-panel");
		append(panel, el("h3", "ee-fb-section-title", "Triage"));

		const erpnextBox = checkbox(true);
		const tritonBox = checkbox(false);
		const targets = el("div", "ee-fb-targets");
		append(
			targets,
			this.targetRow(erpnextBox, `ERPNext — ${detail.projects.erpnext}`),
			this.targetRow(tritonBox, `Triton — ${detail.projects.triton}`)
		);

		const reason = textarea("Why, if you are turning it down. The requester is told.", "", 3);

		const approve = button("Approve and plan", "ee-fb-btn ee-fb-btn-primary", async () => {
			await this.decide(approve, detail.name, {
				decision: "approve",
				target_erpnext: erpnextBox.checked ? 1 : 0,
				target_triton: tritonBox.checked ? 1 : 0,
				reason: reason.value,
			});
		});
		const reject = button("Reject", "ee-fb-btn", async () => {
			await this.decide(reject, detail.name, { decision: "reject", reason: reason.value });
		});

		append(
			panel,
			el("p", "ee-fb-hint", "Approving asks Triton for a breakdown. Nothing reaches a project until you confirm it."),
			targets,
			field("Note", reason).row,
			append(el("div", "ee-fb-actions"), approve, reject)
		);
		return panel;
	}

	targetRow(box, label) {
		const row = el("label", "ee-fb-target");
		append(row, box, el("span", null, label));
		return row;
	}

	duplicatePanel(detail) {
		const panel = el("section", "ee-fb-section ee-fb-panel ee-fb-panel-warn");
		append(
			panel,
			el("h3", "ee-fb-section-title", "Possibly already covered"),
			el("p", "ee-fb-hint", "Closing against one of these creates nothing and tells the requester where the work already is.")
		);
		for (const candidate of detail.duplicate_candidates) {
			const row = el("div", "ee-fb-duplicate");
			append(
				row,
				el("span", `ee-fb-confidence ee-fb-confidence-${String(candidate.confidence).toLowerCase()}`, candidate.confidence),
				deskLink(`${candidate.task} — ${candidate.task_subject}`, `/app/task/${candidate.task}`),
				el("span", "ee-fb-why", candidate.why)
			);
			const close = button("Close as duplicate of this", "ee-fb-btn ee-fb-btn-small", async () => {
				await this.decide(close, detail.name, {
					decision: "duplicate",
					duplicate_of_task: candidate.task,
					reason: candidate.why,
				});
			});
			append(row, close);
			panel.appendChild(row);
		}
		return panel;
	}

	waitingPanel(detail) {
		const panel = el("section", "ee-fb-section ee-fb-panel");
		append(
			panel,
			el("h3", "ee-fb-section-title", "Planning…"),
			el(
				"p",
				"ee-fb-hint",
				"Triton is breaking this into tasks. It usually takes under a minute; if it is still here in fifteen, the hourly sweeper re-runs it."
			),
			append(
				el("div", "ee-fb-actions"),
				button("Refresh", "ee-fb-btn", () => this.renderRequest(detail.name))
			)
		);
		return panel;
	}

	failedPanel(detail) {
		const panel = el("section", "ee-fb-section ee-fb-panel ee-fb-panel-bad");
		const rerun = button("Run it again", "ee-fb-btn ee-fb-btn-primary", async () => {
			this.setBusy(rerun, true, "Queued…");
			try {
				await call(M.RERUN, { name: detail.name });
				await this.renderRequest(detail.name);
			} catch (e) {
				this.showBanner(e.message, "bad");
			} finally {
				this.setBusy(rerun, false, "Run it again");
			}
		});
		append(
			panel,
			el("h3", "ee-fb-section-title", "The breakdown failed"),
			el("p", "ee-fb-reason", detail.breakdown_error || "No reason was recorded."),
			append(el("div", "ee-fb-actions"), rerun)
		);
		return panel;
	}

	// ------------------------------------------------------------------ proposal

	proposalPanel(detail) {
		const panel = el("section", "ee-fb-section ee-fb-panel");
		append(panel, el("h3", "ee-fb-section-title", "Proposed work"));
		if (detail.breakdown_summary) append(panel, el("p", "ee-fb-summary", detail.breakdown_summary));
		if (detail.breakdown_error) {
			append(panel, el("p", "ee-fb-hint", `Dropped on the way: ${detail.breakdown_error}`));
		}

		const editors = [];
		const rows = el("div", "ee-fb-rows");
		for (const row of detail.proposed_tasks) {
			const editor = this.proposalRow(row, detail);
			editors.push(editor);
			rows.appendChild(editor.node);
		}

		const collect = () => editors.map((editor) => editor.read()).filter(Boolean);

		const save = button("Save edits", "ee-fb-btn", async () => {
			this.setBusy(save, true, "Saving…");
			try {
				const result = await call(M.SAVE_PROPOSAL, { name: detail.name, rows: collect() });
				this.reportRejected(result.rejected);
				this.showBanner("Saved.", "ok");
			} catch (e) {
				this.showBanner(e.message, "bad");
			} finally {
				this.setBusy(save, false, "Save edits");
			}
		});

		const create = button("Create these tasks", "ee-fb-btn ee-fb-btn-primary", async () => {
			this.setBusy(create, true, "Creating…");
			try {
				const result = await call(M.CREATE_TASKS, { name: detail.name, rows: collect() }, { timeout: 120000 });
				this.reportRejected(result.rejected);
				if (result.failures && result.failures.length) {
					this.showBanner(
						`Created ${result.created.length}. Some rows failed: ${result.failures.join(" ")}`,
						"warn"
					);
				} else {
					this.showBanner(`Created ${result.created.length} task${result.created.length === 1 ? "" : "s"}.`, "ok");
				}
				const refreshed = await call(M.BOOTSTRAP);
				this.applyBootstrap(refreshed);
				await this.renderRequest(detail.name);
			} catch (e) {
				this.showBanner(e.message, "bad");
			} finally {
				this.setBusy(create, false, "Create these tasks");
			}
		});

		const rerun = button("Ask again", "ee-fb-btn", async () => {
			await call(M.RERUN, { name: detail.name });
			await this.renderRequest(detail.name);
		});

		append(panel, rows, append(el("div", "ee-fb-actions"), create, save, rerun));
		return panel;
	}

	proposalRow(row, detail) {
		const node = el("div", "ee-fb-row");
		if (row.created_task) node.classList.add("ee-fb-row-done");

		const include = checkbox(!!row.include);
		const subject = input("text", "Subject", row.subject);
		const project = select(
			[
				{ value: detail.projects.erpnext, label: `ERPNext (${detail.projects.erpnext})` },
				{ value: detail.projects.triton, label: `Triton (${detail.projects.triton})` },
			],
			row.project
		);
		const priority = select(PRIORITIES, row.priority || "Medium");
		const hours = input("number", "0", row.expected_hours || 0);
		hours.min = "0";
		hours.step = "0.5";

		const top = el("div", "ee-fb-row-top");
		append(top, include, subject, project, priority, hours);

		const placement = el("div", "ee-fb-row-meta");
		if (row.parent_task) {
			append(placement, el("span", null, "under "), deskLink(row.parent_task, `/app/task/${row.parent_task}`));
		} else if (row.group_subject) {
			append(placement, el("span", null, `in a new group: ${row.group_subject}`));
		} else {
			append(placement, el("span", null, "top level"));
		}
		if (row.depends_on_idx) append(placement, el("span", null, ` · after row ${row.depends_on_idx}`));
		if (row.created_task) {
			append(placement, el("span", null, " · created "), deskLink(row.created_task, `/app/task/${row.created_task}`));
		}

		append(node, top, placement, richText(row.description));

		// A created row is a record of what was written, so nothing on it is editable.
		if (row.created_task) {
			for (const control of [include, subject, project, priority, hours]) control.disabled = true;
		}

		return {
			node,
			read() {
				if (row.created_task) return null;
				return {
					name: row.name,
					include: include.checked ? 1 : 0,
					subject: subject.value,
					project: project.value,
					priority: priority.value,
					expected_hours: hours.value,
				};
			},
		};
	}

	createdPanel(detail) {
		const panel = el("section", "ee-fb-section");
		append(panel, el("h3", "ee-fb-section-title", "On the board"));
		const list = el("ul", "ee-fb-created");
		for (const row of detail.proposed_tasks) {
			if (!row.created_task) continue;
			const item = el("li");
			append(item, deskLink(`${row.created_task} — ${row.subject}`, `/app/task/${row.created_task}`));
			list.appendChild(item);
		}
		append(panel, list);
		return panel;
	}

	// ------------------------------------------------------------------ helpers

	async decide(control, name, args) {
		const label = control.textContent;
		this.setBusy(control, true, "Working…");
		try {
			await call(M.DECIDE, { name, ...args });
			const refreshed = await call(M.BOOTSTRAP);
			this.applyBootstrap(refreshed);
			await this.renderRequest(name);
		} catch (e) {
			this.showBanner(e.message, "bad");
		} finally {
			this.setBusy(control, false, label);
		}
	}

	reportRejected(rejected) {
		if (rejected && rejected.length) {
			this.showBanner(`The server refused some fields: ${rejected.join(", ")}`, "warn");
		}
	}

	setBusy(control, busy, label) {
		this.state.busy = busy;
		if (!control) return;
		control.disabled = busy;
		control.textContent = label;
	}

	notice(title, body) {
		const node = el("div", "ee-fb-notice");
		append(node, el("h3", "ee-fb-section-title", title), el("p", null, body));
		return node;
	}

	showBanner(message, tone) {
		this.banner.className = `ee-fb-banner ee-fb-banner-${tone || "ok"}`;
		this.banner.textContent = message;
		this.banner.hidden = false;
	}

	clearBanner() {
		this.banner.hidden = true;
		this.banner.textContent = "";
	}

	fatal(error) {
		clear(this.pane);
		const message =
			error instanceof FeedbackCallError && error.message
				? error.message
				: "Something went wrong loading this page.";
		append(this.pane, this.notice("Could not load", message));
	}
}
