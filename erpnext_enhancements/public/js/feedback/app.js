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
	VIEW_ALL,
	VIEW_REQUEST,
	parseRoute,
	buildRoute,
	landingView,
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
	shortDate,
	statusPill,
	taskPill,
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
			// Admin-view filters. Not in the URL: a filtered admin list is something you scan,
			// not something you link somebody to.
			allFilters: { search: "", status: "", request_type: "", start: 0 },
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

		// The one place the landing default is applied. Only a *bare* `/feedback` is
		// redirected, and the URL is corrected so a refresh lands in the same place.
		const preferred = landingView(
			window.location.pathname,
			this.state.isReviewer,
			this.state.myRequests.length > 0
		);
		if (preferred) window.history.replaceState({}, "", buildRoute(preferred));

		this.routeTo(window.location.pathname);
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
			tabs.push({ view: VIEW_ALL, label: "All requests" });
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

	/**
	 * Resolve a path and render it. **Does not second-guess the caller.**
	 *
	 * The landing default lives in `mount()` and nowhere else. It used to live here, and the
	 * two rules composed into a bug: "New request" linked to the bare `/feedback`, this method
	 * read that as a fresh landing, and a reviewer was sent straight back to the queue — so the
	 * tab appeared to do nothing and the form was unreachable for anyone who reviews. A router
	 * that keeps overriding the view somebody just clicked is not routing.
	 */
	routeTo(pathname) {
		const route = parseRoute(String(pathname).split("?")[0]);
		this.state.view = route.view;
		this.state.name = route.name;
		this.renderNav();
		this.clearBanner();

		if (route.view === VIEW_REQUEST) return this.renderRequest(route.name);
		if (route.view === VIEW_MINE) return this.renderMine();
		if (route.view === VIEW_REVIEW) return this.renderReview();
		if (route.view === VIEW_ALL) return this.renderAll();
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

		// Expands the title into a fuller description. Fills the textarea rather than
		// submitting anything — the requester edits it and is still the author.
		const draft = button("Expand with AI", "ee-fb-btn ee-fb-btn-small", async () => {
			this.setBusy(draft, true, "Drafting…");
			try {
				const result = await call(
					M.DRAFT,
					{
						title: titleInput.value,
						description: descInput.value,
						request_type: typeInput.value,
					},
					{ timeout: 90000 }
				);
				descInput.value = result.description;
				descInput.focus();
				this.showBanner("Drafted from your title. Edit anything that is not right — you are the author.", "ok");
			} catch (e) {
				this.showBanner(e.message, "bad");
			} finally {
				this.setBusy(draft, false, "Expand with AI");
			}
		});
		const descriptionField = field("Description", descInput);
		const descriptionTools = el("div", "ee-fb-field-tools");
		append(
			descriptionTools,
			draft,
			el("span", "ee-fb-field-help", "Write a title, then expand it if you want a hand.")
		);
		append(descriptionField.row, descriptionTools);

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
			descriptionField.row,
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

	/**
	 * Every request, filterable — the admin view.
	 *
	 * Deliberately not the review queue. That one is work to do and excludes terminal states;
	 * this one includes them, because the questions it answers are historical: what did we turn
	 * down, what has this person filed, what came of it.
	 *
	 * Paged server-side. The filter state lives on `this.state.allFilters` rather than in the
	 * URL — a filtered admin list is a thing you scan, not a thing you link somebody to, and
	 * putting it in the URL would mean reconciling it with the router on every keystroke.
	 */
	async renderAll() {
		clear(this.pane);
		if (!this.state.isReviewer) {
			append(this.pane, this.notice("Not for you", "Only a System Manager can see every request."));
			return;
		}
		append(this.pane, el("div", "ee-fb-loading", "Loading…"));

		let data;
		try {
			data = await call(M.ALL, this.state.allFilters);
		} catch (e) {
			clear(this.pane);
			append(this.pane, this.notice("Could not load", e.message));
			return;
		}

		clear(this.pane);
		append(this.pane, this.allFilterBar(data), this.allTally(data), this.allTable(data));
		if (data.total > data.page_size) append(this.pane, this.allPager(data));
	}

	allFilterBar(data) {
		const bar = el("div", "ee-fb-filters");
		const apply = (patch) => {
			// Any filter change resets to page one: staying on page 4 of a narrower result set
			// lands on an empty screen that reads as "nothing matches".
			this.state.allFilters = { ...this.state.allFilters, ...patch, start: 0 };
			this.renderAll();
		};

		const search = input("search", "Title or ER-2026-…", this.state.allFilters.search || "");
		search.addEventListener("keydown", (ev) => {
			if (ev.key === "Enter") apply({ search: search.value });
		});

		const status = select(
			[{ value: "", label: "Any status" }].concat(
				(data.statuses || []).map((s) => ({ value: s, label: s }))
			),
			this.state.allFilters.status || ""
		);
		status.addEventListener("change", () => apply({ status: status.value }));

		const kind = select(
			[{ value: "", label: "Any type" }].concat(
				(data.request_types || []).map((t) => ({ value: t, label: t }))
			),
			this.state.allFilters.request_type || ""
		);
		kind.addEventListener("change", () => apply({ request_type: kind.value }));

		append(
			bar,
			search,
			status,
			kind,
			button("Search", "ee-fb-btn ee-fb-btn-small", () => apply({ search: search.value })),
			button("Clear", "ee-fb-btn ee-fb-btn-small", () => apply({ search: "", status: "", request_type: "" }))
		);
		return bar;
	}

	allTally(data) {
		const tally = el("div", "ee-fb-tally");
		const counts = data.counts || {};
		// Whole-table, not filtered — it is the denominator the page is read against.
		for (const status of data.statuses || []) {
			if (!counts[status]) continue;
			const chip = statusPill(status);
			append(chip, el("span", "ee-fb-tally-n", ` ${counts[status]}`));
			tally.appendChild(chip);
		}
		const showing = data.rows.length;
		append(
			tally,
			el(
				"span",
				"ee-fb-tally-total",
				`${data.start + 1}–${data.start + showing} of ${data.total}`
			)
		);
		return tally;
	}

	allTable(data) {
		if (!data.rows.length) {
			return this.notice("Nothing matches", "Try clearing the filters.");
		}
		const table = el("table", "ee-fb-table");
		const thead = el("thead");
		const hrow = el("tr");
		for (const label of ["Request", "Type", "Impact", "Filed by", "Status", "Work", "Filed"]) {
			const th = el("th", null, label);
			th.scope = "col";
			hrow.appendChild(th);
		}
		append(thead, hrow);
		append(table, thead);

		const tbody = el("tbody");
		for (const row of data.rows) {
			const tr = el("tr", "ee-fb-table-row");

			const first = el("td", "ee-fb-cell-task");
			append(
				first,
				link(row.title || row.name, buildRoute(VIEW_REQUEST, row.name), (href) => this.navigate(href)),
				el("span", "ee-fb-task-name", ` ${row.name}`)
			);
			append(tr, first);

			append(tr, el("td", "ee-fb-cell-quiet", row.request_type || ""));
			append(tr, el("td", "ee-fb-cell-quiet", row.impact || ""));
			append(tr, el("td", "ee-fb-cell-quiet", row.requester_name || ""));

			const status = el("td");
			append(status, statusPill(row.status));
			append(tr, status);

			append(tr, el("td", "ee-fb-cell-quiet", this.workSummary(row)));
			append(tr, el("td", "ee-fb-cell-quiet", relativeTime(row.creation)));
			tbody.appendChild(tr);
		}
		append(table, tbody);

		const scroller = el("div", "ee-fb-table-wrap");
		append(scroller, table);
		return scroller;
	}

	/** "3/7" once work exists, the closing reason when it never will, else blank. */
	workSummary(row) {
		const tasks = row.tasks || { created: 0, done: 0 };
		if (tasks.created) return `${tasks.done}/${tasks.created}`;
		if (row.status === "Duplicate") return row.duplicate_of_task || "duplicate";
		if (row.status === "Rejected") return "—";
		return "";
	}

	allPager(data) {
		const pager = el("div", "ee-fb-actions");
		const go = (start) => {
			this.state.allFilters = { ...this.state.allFilters, start };
			this.renderAll();
		};
		const prev = button("Previous", "ee-fb-btn ee-fb-btn-small", () =>
			go(Math.max(0, data.start - data.page_size))
		);
		prev.disabled = data.start <= 0;
		const next = button("Next", "ee-fb-btn ee-fb-btn-small", () => go(data.start + data.page_size));
		next.disabled = data.start + data.rows.length >= data.total;
		append(pager, prev, next);
		return pager;
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
		if ((detail.created_tasks || []).length) {
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

	/**
	 * What this request actually put on the board, with live status.
	 *
	 * Reads `detail.created_tasks`, which the server builds from the **Task** rows rather than
	 * from the proposal — see `api/feedback._created_task_rows`. The proposal is a frozen
	 * record of what was agreed; this panel answers what has happened since, and the two are
	 * not the same question. Rendering the proposal's copy is why this panel used to show no
	 * status and never changed.
	 */
	createdPanel(detail) {
		const rows = detail.created_tasks || [];
		const panel = el("section", "ee-fb-section");

		let done = 0;
		let live = 0;
		for (const row of rows) {
			if (row.missing || row.is_group) continue;
			live += 1;
			if (row.done) done += 1;
		}

		const head = el("div", "ee-fb-created-head");
		append(head, el("h3", "ee-fb-section-title", "On the board"));
		if (live) {
			const complete = done === live;
			const count = el(
				"span",
				`ee-fb-progress${complete ? " ee-fb-progress-done" : ""}`,
				complete ? `All ${live} complete` : `${done} of ${live} complete`
			);
			append(head, count);
		}
		// Status is read at load. Without this the only way to see a task move is a full
		// reload, which is the complaint this panel was rebuilt for.
		const refresh = button("Refresh", "ee-fb-btn ee-fb-btn-small", () => this.renderRequest(detail.name));
		append(head, refresh);
		append(panel, head);

		const table = el("table", "ee-fb-table");
		const thead = el("thead");
		const hrow = el("tr");
		for (const label of ["Task", "Status", "Priority", "Due"]) {
			const th = el("th", null, label);
			th.scope = "col";
			hrow.appendChild(th);
		}
		append(thead, hrow);
		append(table, thead);

		const tbody = el("tbody");
		for (const row of rows) {
			tbody.appendChild(this.createdRow(row));
		}
		append(table, tbody);

		// Wide content scrolls inside its own container; the page body never scrolls sideways.
		const scroller = el("div", "ee-fb-table-wrap");
		append(scroller, table);
		append(panel, scroller);
		return panel;
	}

	createdRow(row) {
		const tr = el("tr", "ee-fb-table-row");
		if (row.is_group) tr.classList.add("ee-fb-table-group");
		if (row.done) tr.classList.add("ee-fb-table-done");
		if (row.missing) tr.classList.add("ee-fb-table-missing");

		const first = el("td", `ee-fb-cell-task ee-fb-depth-${row.depth || 0}`);
		if (row.missing) {
			// Deleting a generated task is normal — it is the first thing anybody does after a
			// test run — so say so rather than quietly shrinking the table.
			append(
				first,
				el("span", "ee-fb-task-name", row.name),
				el("span", "ee-fb-task-gone", " — no longer on the board")
			);
		} else {
			append(
				first,
				deskLink(row.subject || row.name, `/app/task/${row.name}`),
				el("span", "ee-fb-task-name", ` ${row.name}`)
			);
		}
		append(tr, first);

		const status = el("td");
		append(status, row.missing ? el("span", "ee-fb-pill ee-fb-task-gone-pill", "deleted") : taskPill(row.status));
		append(tr, status);

		append(tr, el("td", "ee-fb-cell-quiet", row.missing ? "" : row.priority || ""));
		append(tr, el("td", "ee-fb-cell-quiet", row.missing ? "" : shortDate(row.exp_end_date)));
		return tr;
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
