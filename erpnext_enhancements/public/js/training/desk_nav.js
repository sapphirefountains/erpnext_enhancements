// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// TR.deskNav — the Training rail. One sidebar, shared by every Desk surface in
// the module, mounted into the page's own `layout-side-section`.
//
// WHY IT EXISTS. After v1.433.0 the module has four desk surfaces — the learner
// page, the manager dashboard, the authoring canvas and a dozen lists — and no way
// to get from any one of them to any other. A learner halfway through a course who
// wanted their certificate went back to a workspace to find the same page again; a
// manager reading "7 overdue" had no door to the player the number is about. The
// Desk's own left sidebar cannot help: it lists WORKSPACES, so the only way to put
// "My Trainings" in it would be to make My Trainings a workspace, and a workspace
// cannot show you the three courses you personally owe.
//
// WHY IT IS A SHARED FILE AND NOT A METHOD ON EACH PAGE. Two hosts, one vocabulary.
// A rail copied into learn.js and training_insights.js is two rails that agree for
// exactly as long as somebody keeps editing both — and the failure is silent, since
// a nav that is merely out of date still renders.
//
// WHAT IT IS NOT ALLOWED TO DO:
//   * it names no endpoint. The learner section is fed the boot payload the player
//     already fetched (`setLearner`), and every manager link is a route. A sidebar
//     that fired its own read would put a second, unversioned copy of "which
//     courses are mine" in the client — the one question this module answers
//     server-side on purpose, because "open" and "overdue" are predicates and
//     `api/training._open_assignments` is where they are defined.
//   * it draws an entry point with nothing behind it. People, Team activity and
//     Sign-offs appear only when the boot payload says this learner has them. That
//     is the same rule `signoffs_to_record` was added for: fourteen of sixteen
//     people should never see a link that opens an empty list.
//   * it hand-builds no URL. `frappe.set_route`, always — an `<a href="/app/…">`
//     inside the Desk is not intercepted by the router and costs a full reload plus
//     a redirect hop to /desk.
//
// PREFIX IS `tn-`, and note the trap: a naive grep for `tn-` also matches every
// Bootstrap `btn-` in the repo. Anchor on `\btn-` (a word boundary excludes the
// `b`) or on the leading dot in CSS.

(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});

	// Mirrors the `roles` lists on training-insights.json and training-canvas.json.
	// Kept in step by tests/test_training_desk_nav.py rather than by memory: a link
	// this file offers to somebody the Page refuses lands them on "Not permitted",
	// which reads as the feature being broken rather than as not being theirs.
	var MANAGER_ROLES = ["System Manager", "Training Manager", "HR Manager"];
	var AUTHOR_ROLES = ["System Manager", "Training Manager", "Training Author"];

	// The desktop breakpoint. Below it frappe still lays `.layout-main` out as a
	// flex ROW, so an always-open rail would sit beside the player on a phone and
	// take a third of the width from the one surface that is read on a phone.
	var WIDE = "(min-width: 992px)";

	function t(text, args) {
		return typeof window.__ === "function" ? window.__(text, args) : text;
	}

	function el(tag, cls, text) {
		var node = document.createElement(tag);
		if (cls) node.className = cls;
		if (text !== undefined && text !== null) node.textContent = text;
		return node;
	}

	function clear(node) {
		while (node.firstChild) node.removeChild(node.firstChild);
	}

	function myRoles() {
		if (window.frappe && frappe.user_roles) return frappe.user_roles;
		if (window.frappe && frappe.boot && frappe.boot.user) return frappe.boot.user.roles || [];
		return [];
	}

	function hasAny(roles) {
		var mine = myRoles();
		for (var i = 0; i < roles.length; i++) {
			if (mine.indexOf(roles[i]) !== -1) return true;
		}
		return false;
	}

	// Can this person open this Desk Page at all? `frappe.boot.allowed_pages` is
	// built by desk.js `sync_pages()` from `frappe.boot.page_info`, which the server
	// has already filtered through `Page.is_permitted()` -- so it is the runtime
	// truth about the very thing a Page link leads to, rather than a second opinion
	// assembled from role names.
	//
	// IT IS NOT REDUNDANT WITH THE ROLE LISTS BELOW, and the gap it closes was real:
	// `HR Manager` is in MANAGER_ROLES because it is on `training-insights.json`,
	// but it is NOT on `learn.json` -- so a person holding HR Manager and nothing
	// else could open the dashboard, be shown "All courses", "My record" and "Open
	// my training" beside it, and get "Not permitted" from all three. The role lists
	// decide whether a SECTION is drawn; this decides whether a DESTINATION is
	// offered. Two questions, two gates.
	function canOpenPage(name) {
		var pages = (window.frappe && frappe.boot && frappe.boot.allowed_pages) || null;
		// Absent bootinfo is not a licence to offer the link: the cost of guessing
		// wrong is a "Not permitted" modal, which reads as the feature being broken.
		if (!pages || !pages.length) return false;
		return pages.indexOf(name) !== -1;
	}

	// The exact question, asked of frappe rather than inferred from a role. The
	// Manage section is opened by role -- that part mirrors the Page documents --
	// but the links INSIDE it are lists, and a role is a poor proxy for a DocPerm:
	// `HR Manager` opens the dashboard and need not hold read on Training Course,
	// while a Training Learner holds read on fourteen Training doctypes and must
	// still see no Manage section at all. Two different gates for two different
	// questions.
	function canRead(doctype) {
		try {
			return !!frappe.model.can_read(doctype);
		} catch (err) {
			// A bootinfo without the permission map is not a licence to offer the
			// link; the cost of being wrong here is a "Not permitted" page, which
			// reads as the feature being broken.
			return false;
		}
	}

	function today() {
		return frappe.datetime.get_today();
	}

	// What the row says under a course title. Status first, because "Completed" and
	// "Awaiting sign-off" are answers to the question a due date only implies.
	function courseMeta(card) {
		if (card.assignment_status === "Completed" || card.completion) return t("Completed");
		if (!card.due_date) return card.assignment_status || t("Assigned");
		return t("Due {0}", [frappe.datetime.str_to_user(card.due_date)]);
	}

	function isOverdue(card) {
		if (card.assignment_status === "Completed" || card.completion) return false;
		// String compare on two ISO dates. Both sides are "YYYY-MM-DD": `due_date`
		// comes off the assignment untouched and get_today() is the site's date, so
		// this is the same comparison the server makes and not a local-timezone one.
		return !!card.due_date && card.due_date < today();
	}

	function DeskNav(opts) {
		this.page = opts.page;
		// The active surface, in the shape the learner page already computes for its
		// own loop guard: {view, course}.
		this.state = opts.active || {};
		// null is "this surface does not hold the learner payload", which is a
		// different thing from [] ("nothing is assigned to you") and renders
		// differently. The manager dashboard stays null on purpose — see the header.
		this.learner = null;
		this.rows = {};
		this.courses = {};
		this.build();
	}

	DeskNav.prototype.build = function () {
		var me = this;
		$(this.page.wrapper).addClass("tn-host");

		// <details> rather than a class toggle: it is a disclosure, the browser gives
		// it keyboard and screen-reader behaviour for free, and `open` is the one
		// piece of state it has. On a wide screen the summary is hidden by CSS and
		// the rail is simply always open.
		this.details = el("details", "tn-nav");
		this.summary = el("summary", "tn-nav-summary", t("Training menu"));
		this.details.appendChild(this.summary);
		this.body = el("div", "tn-nav-body");
		this.details.appendChild(this.body);

		this.root = el("nav", "tn-rail");
		this.root.setAttribute("aria-label", t("Training"));
		this.root.appendChild(this.details);

		// APPENDED, not prepended. frappe puts its own "Navigate to main content"
		// skip link in this element (Page.setup_page), and that link exists to let a
		// keyboard user jump PAST the navigation -- putting a twelve-link rail in
		// front of it is the one arrangement that makes it useless. It is `sr-only`,
		// so it costs no space above the rail.
		this.page.sidebar.append(this.root);

		this.wide = window.matchMedia(WIDE);
		this.details.open = this.wide.matches;
		var onChange = function (event) {
			// Follows the viewport rather than remembering a choice. Rotating a phone
			// into landscape is not a request to keep the menu shut, and the opposite
			// (a rail pinned open on a narrow screen) is the layout this guards.
			me.details.open = event.matches;
		};
		if (this.wide.addEventListener) this.wide.addEventListener("change", onChange);
		else if (this.wide.addListener) this.wide.addListener(onChange);

		this.render();
	};

	// The learner half of the payload the player already fetched. Called once per
	// boot by the learner page; never called on surfaces that have no learner boot.
	DeskNav.prototype.setLearner = function (boot) {
		this.learner = boot || {};
		this.render();
	};

	DeskNav.prototype.setActive = function (state) {
		this.state = state || {};
		this.paint();
	};

	DeskNav.prototype.render = function () {
		clear(this.body);
		this.rows = {};
		this.courses = {};
		// Each section returns null / an empty list when this person has nothing in
		// it, and an empty heading is the thing being avoided: it tells somebody
		// that something is missing when nothing is.
		var mine = this.mine();
		if (mine) this.body.appendChild(mine);
		var learn = this.learnLinks();
		if (learn.length) this.body.appendChild(this.section(t("Learn"), learn));
		var manage = this.manageLinks();
		if (manage.length) this.body.appendChild(this.section(t("Manage"), manage));
		this.paint();
	};

	// ------------------------------------------------------------- My Trainings

	DeskNav.prototype.mine = function () {
		var assigned = this.learner && this.learner.assigned;
		// Nothing to say and nowhere to send them: this person cannot open the
		// learner page and has no payload from it either. A "My Trainings" heading
		// over a dead end is worse than no heading.
		if (!assigned && !canOpenPage("learn")) return null;

		var section = el("div", "tn-section");
		var head = el("div", "tn-section-head");
		head.appendChild(el("h6", "tn-section-title", t("My Trainings")));

		if (assigned) head.appendChild(el("span", "tn-count", String(assigned.length)));
		section.appendChild(head);

		if (!assigned) {
			// No learner payload here. One link rather than a fetch: see the header.
			// Its own key, not `catalog` -- the Learn section below owns that one, and
			// two elements filed under one key means the first is orphaned from
			// `this.rows` and can never be painted active again.
			section.appendChild(
				this.link({ key: "mine", label: t("Open my training"), route: ["learn"] })
			);
			return section;
		}

		if (!assigned.length) {
			// The server's sentence, not a wall of nothing. "Nothing is assigned to
			// you" is a statement about this person and is the one the player makes
			// too, so the two surfaces do not disagree about an empty catalogue.
			section.appendChild(el("p", "tn-empty", t("Nothing is assigned to you right now.")));
			return section;
		}

		var list = el("ul", "tn-courses");
		assigned.forEach(function (card) {
			list.appendChild(this.courseRow(card));
		}, this);
		section.appendChild(list);
		return section;
	};

	DeskNav.prototype.courseRow = function (card) {
		var item = el("li", "tn-course-item");
		var button = el("button", "tn-course");
		button.type = "button";
		button.appendChild(el("span", "tn-course-title", card.title || card.course));

		var meta = el("span", "tn-course-meta", courseMeta(card));
		if (isOverdue(card)) meta.classList.add("is-bad");
		button.appendChild(meta);

		// Only once there is progress to show. A 0% bar on every untouched course is
		// six identical grey lines that say nothing and cost the row its density.
		var percent = Math.max(0, Math.min(100, Math.round(card.percent_complete || 0)));
		if (percent > 0) {
			var bar = el("span", "tn-course-bar");
			bar.setAttribute("role", "progressbar");
			bar.setAttribute("aria-valuenow", String(percent));
			bar.setAttribute("aria-valuemin", "0");
			bar.setAttribute("aria-valuemax", "100");
			bar.setAttribute("aria-label", t("{0}% complete", [String(percent)]));
			var fill = el("span", "tn-course-bar-fill");
			fill.style.width = percent + "%";
			bar.appendChild(fill);
			button.appendChild(bar);
		}

		button.addEventListener("click", function () {
			// The same route the player's own cards write. On the learner page this
			// arrives back through on_page_show and moves the mounted player; from
			// the dashboard it opens the page at the course.
			frappe.set_route("learn", card.course);
		});

		this.courses[card.course] = button;
		item.appendChild(button);
		return item;
	};

	// ------------------------------------------------------------------ the links

	DeskNav.prototype.learnLinks = function () {
		var b = this.learner || {};
		var links = [
			{ key: "catalog", label: t("All courses"), route: ["learn"] },
			{ key: "record", label: t("My record"), route: ["learn", "record"] },
		];
		// Drawn only when the payload says this person has one. `is_staff` is
		// employment rather than a role, because a customer contact holds Training
		// Learner too and should never be offered a colleague list.
		if (b.is_staff) {
			links.push({ key: "people", label: t("People"), route: ["learn", "people"] });
			links.push({ key: "feed", label: t("Team activity"), route: ["learn", "feed"] });
		}
		if (b.signoffs_to_record) {
			links.push({
				key: "queue",
				label: t("Sign-offs to record"),
				route: ["learn", "queue"],
				count: b.signoffs_to_record,
			});
		}
		return this.reachable(links);
	};

	DeskNav.prototype.manageLinks = function () {
		var manager = hasAny(MANAGER_ROLES);
		var author = hasAny(AUTHOR_ROLES);
		if (!manager && !author) return [];

		var links = [];
		if (manager) links.push({ key: "insights", label: t("Insights"), route: ["training-insights"] });
		if (author) links.push({ key: "canvas", label: t("Course canvas"), route: ["training-canvas"] });
		// Author-gated rather than manager-gated, and deliberately: reviewing a drafted
		// question is authoring work on an unpublished draft, and Training Author is the
		// role the canvas already grants for exactly that. The page's own `roles` carry
		// the same three, so reachable() drops this link for anybody the Page refuses.
		if (author) links.push({ key: "review", label: t("Question review"), route: ["training-review"] });
		links.push({ key: "courses", label: t("Courses"), route: ["List", "Training Course"] });
		links.push({ key: "assignments", label: t("Assignments"), route: ["List", "Training Assignment"] });
		if (manager) {
			// The two statuses the server treats as "waiting on a grader", not one of
			// them — a link that says Work to grade and opens half of it is worse than
			// no link, because the list stops being trustworthy rather than the link
			// stopping being useful. Same filter as the Insights tile.
			links.push({
				key: "grading",
				label: t("Work to grade"),
				route: ["List", "Training Submission", { status: ["in", ["Submitted", "Under Review"]] }],
			});
			links.push({ key: "sessions", label: t("Sessions"), route: ["List", "Training Session"] });
			links.push({ key: "settings", label: t("Settings"), route: ["Form", "Training Settings"] });
		}
		// Every destination checked against what this person can actually open --
		// documents by DocPerm, Pages by the permission-filtered boot map. The role
		// test above decided whether to draw the section at all; it does not decide
		// what goes in it.
		return this.reachable(links);
	};

	DeskNav.prototype.section = function (title, links) {
		var section = el("div", "tn-section");
		var head = el("div", "tn-section-head");
		head.appendChild(el("h6", "tn-section-title", title));
		section.appendChild(head);
		links.forEach(function (spec) {
			section.appendChild(this.link(spec));
		}, this);
		return section;
	};

	// One filter for every link the rail draws, whatever section it is in. A route
	// of one segment is a Desk Page; ["List"|"Form", doctype, ...] is a document.
	DeskNav.prototype.reachable = function (links) {
		return links.filter(function (spec) {
			var head = spec.route[0];
			if (head === "List" || head === "Form") return canRead(spec.route[1]);
			return canOpenPage(head);
		});
	};

	DeskNav.prototype.link = function (spec) {
		// href="#" and preventDefault, the idiom training_insights.js already uses:
		// an anchor so it reads and behaves as a link, but the Desk router does the
		// navigating. A real /desk/... href would work and would also be a full page
		// load, because frappe only intercepts its own generated links.
		var anchor = el("a", "tn-link");
		anchor.href = "#";
		anchor.appendChild(el("span", "tn-link-label", spec.label));
		if (spec.count) anchor.appendChild(el("span", "tn-count", String(spec.count)));
		anchor.addEventListener("click", function (event) {
			event.preventDefault();
			frappe.set_route.apply(frappe, spec.route);
		});
		this.rows[spec.key] = anchor;
		return anchor;
	};

	// --------------------------------------------------------------- active state

	DeskNav.prototype.paint = function () {
		var state = this.state || {};
		// Inside a course no nav LINK is current — the course row is. Keying the
		// catalogue link as active there would say "All courses" while a lesson is
		// open, which is the one place the rail could actively mislead.
		var key = state.course ? null : state.view || "catalog";
		Object.keys(this.rows).forEach(function (name) {
			this.rows[name].classList.toggle("is-active", name === key);
		}, this);
		Object.keys(this.courses).forEach(function (name) {
			this.courses[name].classList.toggle("is-active", name === state.course);
		}, this);
	};

	TR.deskNav = function (opts) {
		return new DeskNav(opts);
	};
})();
