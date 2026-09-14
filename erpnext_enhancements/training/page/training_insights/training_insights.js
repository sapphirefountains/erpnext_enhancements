// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Insights — /desk/training-insights. The manager's half of the move.
//
// WHY IT MOVED. `www/training_analytics.html` was 233 lines of server-rendered Jinja
// at a website route, manager-only, with no desk link and no workspace entry.
// Its entire audience is desk users: the manager set is System Manager, Training
// Manager and HR Manager, and a non-manager gets a 404. So it was a page for people
// who live in the Desk, that could only be reached by typing a URL nobody had.
//
// WHAT IT KEEPS. One whitelisted read, `training.analytics.get_training_analytics`,
// unchanged. This page renders the dict it returns and computes nothing of its own.
// That is deliberate and not laziness: the rollup is Python over guarded `get_all`
// reads rather than SQL, because "overdue" is a PREDICATE -- a `<` filter on a
// nullable date silently matches NULLs through frappe's ifnull sentinel, which is
// how "no expiry" once became "expired" across this module. A second implementation
// here, in JavaScript, would be a second chance to get that wrong.
//
// WHAT IT DELIBERATELY DOES NOT KEEP. The Aurora palette. `player.css` is the
// learner's reading surface; this is a manager's console and takes frappe's own
// tokens, so it looks like every other desk dashboard and costs no extra asset load.
// Hence `ti-` and no `--tr-*` anywhere.
//
// Numbers here are CLICKABLE. A manager reading "7 overdue" and then hand-building
// the filter to find out who is the difference between a dashboard and a report.

// The shared Training rail, in a stylesheet and a script. Loaded here rather than
// included globally: TR.loadAssets is already in the desk bundle and can pull
// these on the two pages that want them, which is two files on a training page
// instead of two files on every desk page in the app.
const TI_NAV_ASSETS = [
	"/assets/erpnext_enhancements/css/training/desk_nav.css",
	"/assets/erpnext_enhancements/js/training/desk_nav.js",
];

frappe.pages["training-insights"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Training Insights"),
		// Two-column for the rail. A manager reading "7 overdue" had no door to the
		// player the number is about, and no way back to a course without a
		// workspace in between.
		single_column: false,
	});
	wrapper.training_insights = new TrainingInsights(page);
	ti_mount_nav(page);
};

function ti_mount_nav(page) {
	// This page had no dependency on TR at all before the rail. Guarded so that
	// stays true in effect: a missing global bundle must not turn the dashboard
	// into a blank page, it must cost the sidebar and nothing else.
	if (!window.TR || typeof TR.loadAssets !== "function") return;
	const version = (frappe.boot.versions && frappe.boot.versions.erpnext_enhancements) || "0";
	TR.loadAssets(TI_NAV_ASSETS, version)
		.then(() => {
			if (typeof TR.deskNav !== "function") return;
			// No `setLearner` here, and that is the design rather than an omission.
			// This page holds no learner boot payload, so its rail offers My
			// Trainings as a DOOR rather than as a list. Filling it would mean a
			// second read of every course this manager is assigned, on a page whose
			// whole job is other people's training — and it would put a second
			// client-side answer to "which courses are mine" beside the server's.
			TR.deskNav({ page: page, active: { view: "insights" } });
		})
		.catch(() => {
			// Navigation is not the page. A missing rail must not put an error
			// where six numbers should be.
		});
}

frappe.pages["training-insights"].on_page_show = function (wrapper) {
	if (wrapper.training_insights) wrapper.training_insights.refresh();
};

// tile key -> the Training Assignment list filter it opens. `null` means the number
// is not a filter over one doctype and stays plain text: inventing a filter that
// *looks* like the tile but selects a different set is worse than no link at all.
const TI_DRILL = {
	learners: null,
	active: { status: ["in", ["Not Started", "In Progress"]] },
	completed: { status: "Completed" },
	overdue: { status: "Overdue" },
	awaiting_signoff: { status: "Awaiting Sign-off" },
	certificates: null,
};

const TI_TILES = [
	["learners", __("Learners")],
	["active", __("In progress")],
	["overdue", __("Overdue")],
	["awaiting_signoff", __("Awaiting sign-off")],
	["completed", __("Completed")],
	["certificates", __("Valid certificates")],
];

class TrainingInsights {
	constructor(page) {
		this.page = page;
		this.$body = $('<div class="ti-page"></div>').appendTo(page.body);
		this.loading = false;
		this.page.set_secondary_action(__("Refresh"), () => this.refresh(true));
	}

	refresh(force) {
		// on_page_show fires on every route change into the page. Re-reading the
		// whole org's assignments on each of those would be a lot of work to answer
		// a question whose answer has not moved.
		if (this.loading) return;
		if (this.loaded && !force) return;
		this.loading = true;
		this.page.set_indicator(__("Loading…"), "blue");

		frappe
			.xcall("erpnext_enhancements.training.analytics.get_training_analytics")
			.then((data) => {
				this.loaded = true;
				this.render(data || {});
			})
			.catch(() => {
				// xcall has already shown the server's message. Leave a line behind so
				// the page is not silently blank.
				this.$body.empty().append(
					$('<div class="ti-empty"></div>').text(__("Could not load training analytics."))
				);
				this.page.set_indicator(__("Unavailable"), "red");
			})
			.finally(() => {
				this.loading = false;
			});
	}

	render(data) {
		this.$body.empty();
		this.page.clear_indicator();

		if (data.enabled === false) {
			// The server says so rather than this page guessing. A wall of zeros reads
			// as "nobody is doing any training", which is a different and much worse
			// statement than "training is switched off".
			this.$body.append(
				$('<div class="ti-empty"></div>').text(
					__("Training is switched off, so there is nothing to report yet.")
				)
			);
			return;
		}

		this.render_tiles(data.totals || {});
		this.render_courses(data.by_course || []);
		this.render_batches(data.by_batch);
		this.render_submissions(data.submissions);
		this.render_recent(data.recent_completions || []);

		if (data.generated_on) {
			this.$body.append(
				$('<div class="ti-meta"></div>').text(
					__("As of {0}", [frappe.datetime.str_to_user(data.generated_on)])
				)
			);
		}
	}

	render_tiles(totals) {
		const $row = $('<div class="ti-tiles"></div>').appendTo(this.$body);
		TI_TILES.forEach(([key, label]) => {
			const value = frappe.utils.cint(totals[key]);
			const filter = TI_DRILL[key];
			const $tile = $(
				filter ? '<button type="button" class="ti-tile is-clickable"></button>' : '<div class="ti-tile"></div>'
			);
			$tile.append($('<div class="ti-tile-value"></div>').text(value));
			$tile.append($('<div class="ti-tile-label"></div>').text(label));
			if (key === "overdue" && value > 0) $tile.addClass("is-bad");
			if (filter) {
				$tile.on("click", () => frappe.set_route("List", "Training Assignment", filter));
			}
			$row.append($tile);
		});
	}

	section(title) {
		this.$body.append($('<h3 class="ti-section"></h3>').text(title));
		return $('<div class="ti-table-wrap"></div>').appendTo(this.$body);
	}

	render_courses(rows) {
		if (!rows.length) return;
		const $wrap = this.section(__("By course"));
		const $table = $('<table class="ti-table"></table>').appendTo($wrap);
		$table.append(
			$("<thead></thead>").append(
				$("<tr></tr>").append(
					[__("Course"), __("Assigned"), __("Completed"), __("Overdue"), __("Completion"), __("Avg score")].map(
						(h) => $("<th></th>").text(h)
					)
				)
			)
		);
		const $tbody = $("<tbody></tbody>").appendTo($table);
		rows.forEach((row) => {
			const $tr = $("<tr></tr>").appendTo($tbody);
			$tr.append(
				$("<td></td>").append(
					$('<a class="ti-link" href="#"></a>')
						.text(row.title)
						.on("click", (event) => {
							event.preventDefault();
							frappe.set_route("Form", "Training Course", row.course);
						})
				)
			);
			$tr.append($("<td></td>").text(row.assigned));
			$tr.append($("<td></td>").text(row.completed));
			const $overdue = $("<td></td>").text(row.overdue).appendTo($tr);
			if (row.overdue > 0) $overdue.addClass("is-bad");
			$tr.append($("<td></td>").append(this.bar(row.completion_rate)));
			// null is "nobody has finished it yet", which is not the same as 0%.
			$tr.append($("<td></td>").text(row.avg_score === null ? "—" : `${row.avg_score}%`));
		});
	}

	bar(percent) {
		const value = Math.max(0, Math.min(100, frappe.utils.cint(percent)));
		const $wrap = $('<div class="ti-bar"></div>');
		$wrap.attr("role", "progressbar");
		$wrap.attr("aria-valuenow", value);
		$wrap.attr("aria-valuemin", 0);
		$wrap.attr("aria-valuemax", 100);
		$('<div class="ti-bar-fill"></div>').css("width", `${value}%`).appendTo($wrap);
		$('<span class="ti-bar-text"></span>').text(`${value}%`).appendTo($wrap);
		return $wrap;
	}

	render_batches(batches) {
		// null means the batch doctypes are not migrated; [] means no cohort is
		// active. Neither is worth a heading.
		if (!batches || !batches.length) return;
		const $wrap = this.section(__("Active cohorts"));
		const $table = $('<table class="ti-table"></table>').appendTo($wrap);
		$table.append(
			$("<thead></thead>").append(
				$("<tr></tr>").append(
					[__("Cohort"), __("Members"), __("Courses"), __("Progress"), __("Ends")].map((h) =>
						$("<th></th>").text(h)
					)
				)
			)
		);
		const $tbody = $("<tbody></tbody>").appendTo($table);
		batches.forEach((row) => {
			const $tr = $("<tr></tr>").appendTo($tbody);
			$tr.append(
				$("<td></td>").append(
					$('<a class="ti-link" href="#"></a>')
						.text(row.title)
						.on("click", (event) => {
							event.preventDefault();
							frappe.set_route("Form", "Training Batch", row.batch);
						})
				)
			);
			$tr.append($("<td></td>").text(row.members));
			$tr.append($("<td></td>").text(row.courses));
			$tr.append($("<td></td>").append(this.bar(row.progress)));
			$tr.append($("<td></td>").text(row.end_date || "—"));
		});
	}

	render_submissions(summary) {
		// null means the doctype is not migrated.
		if (!summary) return;
		const $wrap = this.section(__("Work to grade"));
		const $row = $('<div class="ti-tiles"></div>').appendTo($wrap);
		// Each tile opens the set it counts. `pending` is two statuses on the server
		// ("what is waiting on a grader"), so its filter is two statuses here -- a
		// tile that says 4 and opens a list of 11 is worse than a tile that does not
		// open at all, because the number stops being trustworthy rather than the
		// link stopping being useful.
		const filters = {
			pending: { status: ["in", ["Submitted", "Under Review"]] },
			passed: { status: "Passed" },
			needs_rework: { status: "Needs Rework" },
		};
		Object.keys(summary).forEach((key) => {
			const filter = filters[key];
			const $tile = $(
				filter
					? '<button type="button" class="ti-tile is-clickable"></button>'
					: '<div class="ti-tile"></div>'
			);
			$tile.append($('<div class="ti-tile-value"></div>').text(frappe.utils.cint(summary[key])));
			$tile.append($('<div class="ti-tile-label"></div>').text(frappe.unscrub(key)));
			if (filter) {
				$tile.on("click", () => frappe.set_route("List", "Training Submission", filter));
			}
			$row.append($tile);
		});
	}

	render_recent(rows) {
		if (!rows.length) return;
		const $wrap = this.section(__("Recently completed"));
		const $list = $('<ul class="ti-recent"></ul>').appendTo($wrap);
		rows.forEach((row) => {
			$("<li></li>")
				.text(`${row.user} — ${row.course_title} (${row.score_percent}%)`)
				.appendTo($list);
		});
	}
}
