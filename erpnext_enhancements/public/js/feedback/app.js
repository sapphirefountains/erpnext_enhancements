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

/** Mirrors `api.feedback.draft_description`'s own guard. The server stays the authority. */
const MIN_TITLE_FOR_DRAFT = 8;

/** The state of this page's own history entries. */
const HISTORY_KEY = "ee_fb";
/**
 * The report panel's (`capture/panel.js`, `HISTORY_KEY`). While it is open the panel pushes
 * `{ee_capture: <id>}` with no URL over this page, and answers Back itself.
 */
const CAPTURE_KEY = "ee_capture";
/**
 * The sessionStorage key the New form's draft is mirrored under, with the user after a colon
 * (`storeDraft`). Per tab, so it outlives Back off the page and a reload, and no other tab sees it.
 */
const DRAFT_KEY = "ee_fb_new_draft";
/**
 * Shown over a New form restored from a mirror that was being submitted when the page was left
 * (`maybe_filed`). The browser drops the reply with the page, but the server has usually filed it
 * by then, so the text is kept, and the next tap is not an unwitting duplicate.
 */
const MAYBE_FILED =
	"This may already have been filed: it was being sent when the page was left. Check My requests before you send it again.";

export class FeedbackApp {
	constructor(root, boot) {
		this.root = root;
		this.boot = boot || {};
		this.state = {
			user: this.boot.user || "",
			fullName: this.boot.full_name || "",
			isReviewer: false,
			paused: false,
			aiDrafting: false,
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
			// What is typed into the New form, kept until it is sent (`newDraft`).
			newDraft: null,
			busy: false,
		};
		// The path the pane was last drawn for.
		this.here = "";
		// False until `mount()` has applied the bootstrap. Back or Forward before then draws
		// nothing (`onPopState`): the New form built without the bootstrap's Impact choices would
		// be cached as the draft with no impact, and file the first choice, "Blocking my work".
		// `mount()` draws whichever entry the browser is on once it has them.
		this.ready = false;
		// Bumped on every screen change and every fetch-then-draw. A reply that lands after it
		// moved on draws nothing: Back while a request loads must not paint the request over the
		// list the person went back to.
		this.renderToken = 0;
		// The New form last drawn: the draft it was drawn from, the `renderToken` it was drawn for,
		// its description box, and `sync`, which redraws its buttons and attachment list from the
		// draft's work in flight. A late "Expand with AI", an upload or a submit reaches the form
		// on screen through here, not the one it started on.
		this.newForm = null;
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
		window.addEventListener("popstate", (ev) => this.onPopState(ev));
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
		// The entry the page opened on is stamped as this page's, never pushed over, so Back from
		// the first screen leaves the page as it always has. Not while the report panel is open
		// over the page: the entry on top is the panel's then.
		if (!captureOpen()) writeEntry(false, preferred ? buildRoute(preferred) : undefined);

		// From here Back and Forward draw (`onPopState`). One pressed while this loaded moved the
		// address only, so this draws the entry the browser is on now.
		this.ready = true;
		this.routeTo(window.location.pathname);
	}

	applyBootstrap(data) {
		const payload = data || {};
		this.state.isReviewer = !!payload.is_reviewer;
		this.state.paused = !!payload.paused;
		this.state.aiDrafting = !!payload.ai_drafting;
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
		// The report panel's entry is on top while it is open (`onPopState`). An entry pushed now
		// would sit over it: the panel's own Back would land on this page's entry and leave the
		// panel open over the wrong address. Nothing moves until it has closed.
		if (captureOpen()) return;
		// Screens, not strings: the bare `/feedback` a first-time filer lands on shows the form
		// with "New request" lit, but that tab's href is `/feedback/new`.
		const to = parseRoute(href);
		const at = parseRoute(window.location.pathname);
		if (to.view === at.view && to.name === at.name) {
			// Already here: no second entry, or the next Back would appear to do nothing. The
			// address is corrected in place when it names the screen differently (the tap says "I
			// want the form"). The New form is left as it is; a list is drawn again, which is what
			// a second tap asks for.
			if (href !== window.location.pathname) {
				writeEntry(false, href);
				this.here = String(href).split("?")[0];
			}
			if (to.view !== VIEW_NEW) this.routeTo(href);
			return;
		}
		writeEntry(true, href);
		this.routeTo(href);
	}

	/**
	 * Back or Forward. The screen follows the address, except around the report panel
	 * (`capture/panel.js`), which puts an entry of its own over this page while it is open:
	 *
	 *   - While the panel is open, and until the popstate of its own closing Back has landed,
	 *     Back is the panel's to answer ("Discard this report?"). Nothing here moves, so a
	 *     half-written request under it is not cleared by the answer.
	 *   - An entry it left behind (Forward onto it after Back closed the panel, or a reload with
	 *     the panel open) is not a screen of this page. It is re-stamped as this page's, and on
	 *     the address already on screen the view, and whatever is typed in it, stays.
	 *
	 * Nothing is drawn before `mount()` has the bootstrap (`ready`); it draws the entry landed on.
	 */
	onPopState(ev) {
		if (!this.ready) return;
		if (captureOpen()) return;
		const state = ev && ev.state;
		if (state && typeof state === "object" && CAPTURE_KEY in state) {
			writeEntry(false);
			if (window.location.pathname === this.here) return;
		}
		this.routeTo(window.location.pathname);
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
		this.here = String(pathname).split("?")[0];
		const route = parseRoute(this.here);
		this.state.view = route.view;
		this.state.name = route.name;
		this.renderToken += 1;
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
			this.newForm = null;
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

		// Drawn from what was typed last time, so Back, Forward or a tab never wipes a
		// half-written request; each keystroke is kept as it happens, and mirrored (`storeDraft`).
		// And from the work still in flight on it: an upload, an "Expand with AI" or a submit
		// started on a drawing the person has since left carries on, and this one shows it (`sync`).
		const kept = this.newDraft();
		const token = this.renderToken;
		const typeInput = select(this.state.requestTypes, kept.request_type);
		const titleInput = input("text", "One line: what is wrong, or what you want", kept.title);
		titleInput.maxLength = 200;
		const impactInput = select(this.state.impacts, kept.impact);
		const descInput = textarea("What happened, and what you expected instead.", kept.description, 7);
		const stepsInput = textarea("1. Open …\n2. Click …\n3. It does …", kept.steps, 5);
		const keep = (node, key, type) =>
			node.addEventListener(type || "input", () => {
				kept[key] = node.value;
				this.storeDraft(kept);
			});
		keep(typeInput, "request_type", "change");
		keep(titleInput, "title");
		keep(impactInput, "impact", "change");
		keep(descInput, "description");
		keep(stepsInput, "steps");

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
			// Drawn disabled while the draft has work out (`syncDraft`); one tap that lands anyway
			// asks nothing a second time.
			if (this.state.busy || kept.drafting || kept.sending) return;
			// What the description said when it was asked. It takes up to 90 s.
			const sent = descInput.value;
			kept.drafting = true;
			this.setBusy(draft, true, "Drafting…");
			try {
				const result = await call(
					M.DRAFT,
					{
						title: titleInput.value,
						description: sent,
						request_type: typeInput.value,
					},
					{ timeout: 90000 }
				);
				// The form on screen, if it is this draft's: this one, or one drawn again from it
				// after the person left and came back while it drafted. None when they are elsewhere.
				const live = this.liveNewForm(kept);
				// Asked for, so kept, but only if nothing has been typed into the description since
				// it was asked, on this form or any drawn again from the draft: the reply never
				// replaces newer typing. Said when it is on screen, so the wait does not end in silence.
				if ((live ? live.description.value : kept.description) !== sent) {
					if (live) {
						this.showBanner(
							"You typed into the description while it drafted, so your text was kept and the AI draft was not applied.",
							"warn"
						);
					}
					return;
				}
				kept.description = result.description;
				this.storeDraft(kept);
				// The person left the form: it shows the draft when they come back.
				if (!live) return;
				live.description.value = result.description;
				if (live.description === descInput) descInput.focus();
				this.showBanner("Drafted from your title. Edit anything that is not right — you are the author.", "ok");
			} catch (e) {
				if (this.liveNewForm(kept)) this.showBanner(e.message, "bad");
			} finally {
				kept.drafting = false;
				// Re-syncs the form on screen, which may have been drawn again since: its button
				// follows the draft, and the title rule is applied again (`sync`).
				this.setBusy(draft, false, "Expand with AI");
			}
		});
		const descriptionField = field("Description", descInput);
		const descriptionTools = el("div", "ee-fb-field-tools");
		// Hidden entirely when Vertex is unconfigured. A button that cannot work reads as a
		// bug in the feature rather than a missing setting -- which is how it was reported.
		descriptionTools.hidden = !this.state.aiDrafting;
		const draftHelp = el("span", "ee-fb-field-help", "");
		append(descriptionTools, draft, draftHelp);
		append(descriptionField.row, descriptionTools);

		// The button is disabled until there is a title to expand FROM. Without this, clicking
		// it early threw the server's own "write a title first" guard back as a 417 — which is
		// a correct refusal presented as a server error, and it showed up in the console as
		// one. The server keeps that guard (it is the authority; this is a courtesy), so the
		// two thresholds have to agree.
		//
		// Disabled too while this draft has an Expand or a submit out, read from the draft itself
		// and not only from `state.busy`: any other action's `setBusy(..., false)` clears that
		// flag, and a reviewer's decision can land while this form's own work is still out.
		const syncDraft = () => {
			const ready = titleInput.value.trim().length >= MIN_TITLE_FOR_DRAFT;
			draft.disabled = !ready || this.state.busy || !!kept.drafting || !!kept.sending;
			draft.textContent = kept.drafting ? "Drafting…" : "Expand with AI";
			draftHelp.textContent = ready
				? "Expands your title into a description you can edit."
				: "Write a title first, then this can expand it for you.";
		};
		titleInput.addEventListener("input", syncDraft);

		const attachments = this.buildAttachmentPicker(kept);

		const submit = button("Submit", "ee-fb-btn ee-fb-btn-primary", async () => {
			await this.submitRequest({
				kept,
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
		const sendingNote = el("p", "ee-fb-context", "Sending… The form opens again for changes if it does not go through.");
		sendingNote.setAttribute("role", "status");

		// Draws the draft's work in flight onto this form, whichever drawing it started on. While
		// it is being sent the form is read-only, because what is typed then would be dropped when
		// it is filed; while anything is busy, Submit and Expand are drawn disabled rather than
		// swallowing a tap. Called on every change of it (`syncNewForm`, `setBusy`).
		//
		// Submit follows the draft's own work as well as `state.busy`, which another action's
		// `setBusy(..., false)` clears: a submit or Expand still out, and any file still uploading.
		// Sent before a file lands, the request would be filed without it and the file attached to
		// nothing, so Submit waits for it. The upload has no timeout of its own: a dropped
		// connection or a refusal ends it as surely as a success, and a reload lets go of one that
		// never answers (uploads are not mirrored).
		const sync = () => {
			const sending = !!kept.sending;
			const waiting = uploadsPending(kept);
			for (const node of [typeInput, titleInput, impactInput, descInput, stepsInput, attachments.picker]) {
				node.disabled = sending;
			}
			syncDraft();
			submit.disabled = this.state.busy || sending || !!kept.drafting || waiting;
			submit.textContent = sending ? "Submitting…" : waiting ? "Waiting for uploads…" : "Submit";
			sendingNote.hidden = !sending;
			attachments.draw();
		};
		this.newForm = { kept, token, description: descInput, sync };
		sync();

		append(
			form,
			field("Type", typeInput).row,
			field("Title", titleInput).row,
			field("Impact", impactInput, "Your read on it. It informs triage; it does not set the priority of the work.").row,
			descriptionField.row,
			stepsField.row,
			attachments.row,
			this.contextSummary(),
			append(el("div", "ee-fb-actions"), submit),
			sendingNote
		);
		append(this.pane, form);
		// Not under the report panel: focus belongs to what the person is typing into there.
		if (!captureOpen() && !kept.sending) titleInput.focus();
		// Restored from a mirror that was being sent when the page was left (`MAYBE_FILED`). Said
		// on every drawing of it, since leaving the form clears the banner (`routeTo`).
		if (kept.maybe_filed && !kept.sending) this.showBanner(MAYBE_FILED, "warn");
	}

	/**
	 * The New form on screen when it was drawn from `kept`, else null: the person is on another
	 * screen, or the draft was sent and a new one started.
	 */
	liveNewForm(kept) {
		const live = this.newForm;
		return live && live.kept === kept && live.token === this.renderToken ? live : null;
	}

	/** Bring the New form last drawn up to date with its draft's work in flight (`renderNew`). */
	syncNewForm() {
		if (this.newForm) this.newForm.sync();
	}

	/**
	 * What is typed into the New form, kept across screens until it is sent: Back, Forward and
	 * the tabs redraw the form from it. Uploaded files are kept by the name submit sends, with the
	 * file name the list shows.
	 *
	 * Mirrored to this tab's sessionStorage too (`storeDraft`), because memory does not outlive
	 * the page. When `/feedback/new` is the tab's first entry (a link from an email), Back leaves
	 * the page and Forward loads it again — `no_cache` keeps it out of the back-forward cache — so
	 * an empty memory is filled from the mirror before it starts a blank form.
	 *
	 * The work in flight on it lives here too, in memory only and never in the mirror, so a form
	 * drawn again shows it (`renderNew`'s `sync`): `uploads` (each file still uploading, or
	 * refused, as the list shows it), `drafting` (an "Expand with AI" is out) and `sending` (it is
	 * being submitted).
	 *
	 * `maybe_filed` is the one thing about a submit that is mirrored: the mirror says so while one
	 * is out (`storeDraft`), so a draft restored from it was left mid-send, and its form warns
	 * that it may already have been filed (`MAYBE_FILED`). It holds until the draft is filed: a
	 * later send that is refused says nothing about the one whose reply was lost.
	 */
	newDraft() {
		if (!this.state.newDraft) {
			this.state.newDraft = {
				request_type: "Bug",
				title: "",
				impact: this.state.impacts[1] || this.state.impacts[0],
				description: "",
				steps: "",
				attachments: [],
				labels: {},
				uploads: [],
				drafting: false,
				sending: false,
				maybe_filed: false,
				...this.restoreDraft(),
			};
		}
		return this.state.newDraft;
	}

	/** This user's sessionStorage key for the draft, or "" when the page does not know who it is. */
	draftKey() {
		return this.state.user ? `${DRAFT_KEY}:${this.state.user}` : "";
	}

	/**
	 * Mirror `kept` to sessionStorage, if it is still the draft (one already sent is not written
	 * back). The typed fields and each uploaded file's name and label: never a file's contents,
	 * which are on the server already. And `maybe_filed` while a submit is out, or one was when a
	 * restored draft's page was left (`newDraft`): a reply that never arrives, because the page
	 * was left first, cannot clear it. Storage that refuses (a private window, a full quota) leaves
	 * the draft in memory, as it was before there was a mirror.
	 */
	storeDraft(kept) {
		const key = this.draftKey();
		if (!key || !kept || kept !== this.state.newDraft) return;
		const labels = {};
		for (const name of kept.attachments) if (kept.labels[name]) labels[name] = String(kept.labels[name]);
		try {
			window.sessionStorage.setItem(
				key,
				JSON.stringify({
					request_type: kept.request_type,
					title: kept.title,
					impact: kept.impact,
					description: kept.description,
					steps: kept.steps,
					attachments: kept.attachments.slice(),
					labels,
					...(kept.sending || kept.maybe_filed ? { maybe_filed: true } : {}),
				})
			);
		} catch (e) {
			// Kept in memory only.
		}
	}

	/**
	 * The mirrored draft (`storeDraft`), as fields to lay over a blank one: only what is well
	 * formed, and a Type or Impact only while it is still one of the choices. Empty when there is
	 * none or storage cannot be read.
	 */
	restoreDraft() {
		const key = this.draftKey();
		if (!key) return {};
		let saved;
		try {
			saved = JSON.parse(window.sessionStorage.getItem(key) || "null");
		} catch (e) {
			return {};
		}
		if (!saved || typeof saved !== "object") return {};
		const out = {};
		const offered = (options, value) => options.some((o) => (o && typeof o === "object" ? o.value : o) === value);
		if (offered(this.state.requestTypes, saved.request_type)) out.request_type = saved.request_type;
		if (offered(this.state.impacts, saved.impact)) out.impact = saved.impact;
		for (const prop of ["title", "description", "steps"]) {
			if (typeof saved[prop] === "string") out[prop] = saved[prop];
		}
		const names = Array.isArray(saved.attachments) ? saved.attachments : [];
		out.attachments = names.filter((name) => typeof name === "string" && name).slice(0, 5);
		out.labels = {};
		const labels = saved.labels && typeof saved.labels === "object" ? saved.labels : {};
		for (const name of out.attachments) if (typeof labels[name] === "string") out.labels[name] = labels[name];
		if (saved.maybe_filed === true) out.maybe_filed = true;
		return out;
	}

	/** Drop the mirror: the draft was sent. */
	forgetDraft() {
		const key = this.draftKey();
		if (!key) return;
		try {
			window.sessionStorage.removeItem(key);
		} catch (e) {
			// Nothing to drop.
		}
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

	/**
	 * The file picker, and the list under it drawn from the draft (`newDraft`): each uploaded file
	 * as ready, then each still uploading or refused. An upload carries on after the person leaves
	 * the form, and moves the list of whichever drawing of it is on screen (`syncNewForm`). Submit
	 * waits while any is still uploading (`renderNew`'s `sync`), so the files listed as ready are
	 * the files it sends.
	 */
	buildAttachmentPicker(kept) {
		const picker = document.createElement("input");
		picker.type = "file";
		picker.accept = "image/*,.pdf,.txt,.log";
		picker.multiple = true;
		picker.className = "ee-fb-input";

		const list = el("div", "ee-fb-attachments");
		// The draft's own lists, so an upload that finishes while the person is on another screen,
		// or on the form drawn again, is attached and listed where they are.
		const uploaded = kept.attachments;
		const draw = () => {
			clear(list);
			for (const name of uploaded) {
				list.appendChild(el("div", "ee-fb-attachment ee-fb-attachment-ok", `${kept.labels[name] || name} — ready`));
			}
			for (const entry of kept.uploads) {
				list.appendChild(
					el("div", `ee-fb-attachment${entry.bad ? " ee-fb-attachment-bad" : ""}`, `${entry.label} — ${entry.text}`)
				);
			}
		};

		picker.addEventListener("change", async () => {
			const files = Array.from(picker.files || []);
			picker.value = "";
			for (const file of files) {
				if (uploaded.length >= 5) break;
				const entry = { label: file.name, text: "uploading…", bad: false };
				kept.uploads.push(entry);
				this.syncNewForm();
				try {
					// Upload first, link on submit. A file uploaded against nothing is harmless;
					// a request pointing at a file that failed to upload is not.
					const result = await upload(file, (fraction) => {
						entry.text = `${Math.round(fraction * 100)}%`;
						this.syncNewForm();
					}).promise;
					kept.uploads.splice(kept.uploads.indexOf(entry), 1);
					uploaded.push(result.name);
					kept.labels[result.name] = file.name;
					this.storeDraft(kept);
				} catch (e) {
					entry.text = e.message;
					entry.bad = true;
				}
				this.syncNewForm();
			}
		});

		const wrapper = field("Attachments", picker, "A screenshot answers more than a paragraph.");
		append(wrapper.row, list);
		return { row: wrapper.row, names: uploaded, picker, draw };
	}

	async submitRequest({ kept, submit, values, attachments }) {
		// Drawn disabled while any of this is true (`renderNew`'s `sync`). Checked here as well: a
		// tap can land on a drawing another action's `setBusy` re-enabled a moment before.
		if (this.state.busy || kept.sending || kept.drafting || uploadsPending(kept)) return;
		// Sent as it stands now. Until the server answers, the form, and any drawn again from the
		// draft, is read-only (`renderNew`'s `sync`): the filed request replaces it, and a correction
		// typed meanwhile would be dropped without a word. The mirror says it may have been filed
		// until then (`storeDraft`), for the page left before the answer.
		kept.sending = true;
		this.storeDraft(kept);
		this.setBusy(submit, true, "Submitting…");
		try {
			const result = await call(M.SUBMIT, { payload: values, attachments: attachments.names });
			// Filed: the next New form starts empty, here and after a reload.
			this.state.newDraft = null;
			this.forgetDraft();
			// Only the nav counts, which the next refresh corrects. Failing, it must not reach the
			// catch below, which would reopen the form with what was just filed, orphaned from the
			// draft, and file it again on the next tap.
			try {
				this.applyBootstrap(await call(M.BOOTSTRAP));
			} catch (e) {
				// Filed all the same.
			}
			if (result && result.rejected && result.rejected.length) {
				// Reported rather than swallowed — see `api/feedback.py`. A field the server
				// refused is a bug in this file, and a silent one is found weeks later.
				this.showBanner(`Filed, but some fields were not saved: ${result.rejected.join(", ")}`, "warn");
			}
			if (this.state.view !== VIEW_NEW || captureOpen()) {
				// They went to another screen while it was sent, or opened the report panel over
				// the form, whose entry is on top of this page's now: a push would bury it
				// (`navigate`). It was filed all the same: say so where they are, rather than pull
				// them to it. A form still under the panel is drawn again empty, so what was just
				// filed cannot be sent a second time from it.
				if (this.state.view === VIEW_NEW) {
					this.renderToken += 1;
					this.renderNew();
				}
				this.showBanner(`Filed as ${result.name}.`, "ok");
				return;
			}
			this.navigate(buildRoute(VIEW_REQUEST, result.name));
		} catch (e) {
			this.showBanner(e.message, "bad");
		} finally {
			// Not sent, or sent and replaced: either way the form on screen opens again (`setBusy`).
			kept.sending = false;
			// Refused: not filed, so the mirror stops saying it may have been, unless an earlier
			// send's reply was lost (`maybe_filed`). One filed is not written back (`storeDraft`).
			this.storeDraft(kept);
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
		// Only while this list is the screen, and only the newest fetch draws: see `renderToken`.
		if (this.state.view !== VIEW_ALL) return;
		const token = ++this.renderToken;
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
			if (token !== this.renderToken) return;
			clear(this.pane);
			append(this.pane, this.notice("Could not load", e.message));
			return;
		}
		if (token !== this.renderToken) return;

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
		// Deliberately the STORED statuses, so no `statusPill` progress here: these counts
		// come from a group-by and they mirror the status filter beside them, which also
		// filters on the column. A chip reading "Tasks Completed" that the filter could not
		// then select would be worse than one that agrees with the dropdown.
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
			append(status, statusPill(row.status, row.tasks));
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
				statusPill(row.status, row.tasks)
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
		// Only while that request is the screen. A Refresh, decision or re-run that finishes
		// after the person moved on must not draw it over where they went; and only the newest
		// fetch draws (`renderToken`).
		if (this.state.view !== VIEW_REQUEST || this.state.name !== name) return;
		const token = ++this.renderToken;
		clear(this.pane);
		append(this.pane, el("div", "ee-fb-loading", "Loading…"));
		let detail;
		try {
			detail = await call(M.GET, { name });
		} catch (e) {
			if (token !== this.renderToken) return;
			clear(this.pane);
			append(this.pane, this.notice("Cannot open that", e.message));
			return;
		}
		if (token !== this.renderToken) return;
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
		append(head, el("h2", "ee-fb-detail-title", detail.title), statusPill(detail.status, detail.tasks));
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
			const rows = collect();
			if (!rows.some((row) => row.include)) {
				// The server refuses this too; saying it here saves the round trip and
				// names the two ways to close a request that needs no work.
				this.showBanner(
					"Nothing is ticked. Tick at least one task, or reject the request or mark it a duplicate if no work is needed.",
					"warn"
				);
				return;
			}
			this.setBusy(create, true, "Creating…");
			try {
				const result = await call(M.CREATE_TASKS, { name: detail.name, rows }, { timeout: 120000 });
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
		if (control) {
			control.disabled = busy;
			control.textContent = label;
		}
		// The New form may have been drawn again since this started: its Submit and Expand follow
		// the flag too, not only the control that was tapped, which may be off screen now.
		this.syncNewForm();
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

/**
 * True while a file picked into the draft is still uploading (`buildAttachmentPicker`). One that
 * was refused or dropped is listed as such, and waits on nothing.
 */
function uploadsPending(kept) {
	return kept.uploads.some((entry) => !entry.bad);
}

/**
 * Push an entry for `href`, or re-stamp the current one: with `href` when given, else in place
 * with two arguments, keeping the address.
 */
function writeEntry(push, href) {
	const state = { [HISTORY_KEY]: 1 };
	try {
		if (push) window.history.pushState(state, "", href);
		else if (href) window.history.replaceState(state, "", href);
		else window.history.replaceState(state, "");
	} catch (e) {
		// Safari refuses past 100 calls in 10 s. The screen still changes; only the entry is lost.
	}
}

/**
 * True while the report panel is open over the page, and until the popstate of its own closing
 * Back has landed (`capture/panel.js`, `isPanelOpen`). Read through the recorder's global only:
 * this bundle does not load the panel.
 */
function captureOpen() {
	try {
		const capture = window.ee_capture;
		return !!(capture && typeof capture.isOpen === "function" && capture.isOpen());
	} catch (e) {
		return false; // a broken recorder is a closed panel
	}
}
