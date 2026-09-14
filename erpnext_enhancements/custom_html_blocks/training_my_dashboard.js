// My Training — the learner's dashboard, as a Custom HTML Block.
//
// Statistics, then the courses this person still owes, split into Required and
// Optional in the author's own vocabulary (`Training Course.weight`).
//
// WHY A BLOCK AND NOT A WORKSPACE WIDGET. A frappe Workspace can hold Number
// Cards, Quick Lists and Charts, and none of them can answer "which courses does
// the person LOOKING AT THIS owe" — their filters are stored on the widget, the
// same for every viewer. The Desk's own left sidebar has the same limitation and
// for the same reason: it lists workspaces, which are places, not people.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace re-runs
// this whole script with a fresh root on every navigation — so nothing is cached
// across renders and every listener is bound to the fresh DOM each time. Same
// model as the Finance widgets and hr_training_compliance.
//
// IT COMPUTES NOTHING. Every number comes from `training.dashboard`, which counts
// them off the same rows it then sends. That is deliberate: this module has twice
// shipped a dashboard number that disagreed with the list under it, once because
// "overdue" was re-derived in a filter and once because a tile counted four
// statuses while its drill-through selected two.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.training.dashboard.get_my_dashboard";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#tmd-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function esc(value) {
        return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
    }

    function pct(value) {
        return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
    }

    // A tile is drawn only when it has something to say. Six tiles reading zero is
    // a worse answer than three tiles reading something, and "0 overdue" in red is
    // a problem that is not there.
    function tiles(data) {
        const c = data.compliance || {};
        const stats = data.stats || {};
        const scores = data.scores || {};
        const out = [];
        if (c.overdue) out.push({ label: "Overdue", value: c.overdue, bad: true });
        if (c.due_soon) out.push({ label: "Due in 30 days", value: c.due_soon });
        if (c.required_percent !== null && c.required_percent !== undefined) {
            out.push({ label: "Required complete", value: c.required_percent + "%" });
        }
        if (c.certificates_expiring) out.push({ label: "Certificates lapsing", value: c.certificates_expiring, bad: true });
        if (c.minutes_outstanding) out.push({ label: "Minutes to go", value: c.minutes_outstanding });
        if (stats.points) out.push({ label: "Points", value: stats.points });
        if (stats.streak_days) out.push({ label: "Day streak", value: stats.streak_days });
        if (stats.badges_earned) out.push({ label: "Badges", value: stats.badges_earned });
        // The learner's own scores, and nobody else's — `get_person_dashboard` does
        // not build them at all.
        if (scores && scores.average_score !== null && scores.average_score !== undefined) {
            out.push({ label: "Average score", value: scores.average_score + "%" });
        }
        return out;
    }

    function renderTiles(container, data) {
        const host = container.querySelector("#tmd-stats");
        const rows = tiles(data);
        if (!rows.length) {
            host.innerHTML = "";
            return;
        }
        host.innerHTML = rows
            .map(
                (t) =>
                    '<div class="tmd-tile' +
                    (t.bad ? " is-bad" : "") +
                    '"><div class="tmd-tile-value">' +
                    esc(t.value) +
                    '</div><div class="tmd-tile-label">' +
                    esc(t.label) +
                    "</div></div>"
            )
            .join("");
    }

    function courseRow(row) {
        const bits = [];
        if (row.overdue) {
            bits.push('<span class="tmd-flag is-bad">Overdue</span>');
        } else if (row.due_date) {
            bits.push("<span>Due " + esc(frappe.datetime.str_to_user(row.due_date)) + "</span>");
        } else if (row.status) {
            bits.push("<span>" + esc(row.status) + "</span>");
        }
        if (row.minutes) bits.push("<span>" + esc(row.minutes) + " min</span>");

        const done = pct(row.percent_complete);
        const bar = done > 0
            ? '<span class="tmd-bar"><span class="tmd-bar-fill" style="width:' + done + '%"></span></span>'
            : "";
        return (
            '<button type="button" class="tmd-row" data-course="' +
            esc(row.course) +
            '"><span class="tmd-row-title">' +
            esc(row.title) +
            '</span><span class="tmd-row-meta">' +
            bits.join(" · ") +
            "</span>" +
            bar +
            "</button>"
        );
    }

    function section(title, rows, emptyText) {
        if (!rows.length && !emptyText) return "";
        const body = rows.length
            ? rows.map(courseRow).join("")
            : '<div class="tmd-muted">' + esc(emptyText) + "</div>";
        return '<div class="tmd-section"><div class="tmd-section-title">' + esc(title) + "</div>" + body + "</div>";
    }

    function render(container, data) {
        const body = container.querySelector("#tmd-body");
        const sub = container.querySelector("#tmd-sub");

        if (data.enabled === false) {
            // The server's sentence, not a wall of zeros. "You have no training" is
            // a statement about this person and would be wrong.
            sub.textContent = "";
            container.querySelector("#tmd-stats").innerHTML = "";
            body.innerHTML = '<div class="tmd-muted">' + esc(data.message || "Training is switched off.") + "</div>";
            return;
        }

        renderTiles(container, data);
        const required = data.required || [];
        const optional = data.optional || [];
        const completed = data.completed || [];
        sub.textContent = required.length ? required.length + " required outstanding" : "nothing outstanding";

        body.innerHTML =
            section("Required", required, "Nothing required of you right now.") +
            section("Optional", optional, "") +
            // Only the most recent handful: this is a dashboard, and the full record
            // is one click away on My record.
            section("Recently completed", completed.slice(0, 4), "");

        body.querySelectorAll(".tmd-row").forEach((node) => {
            node.addEventListener("click", () => {
                // The route the player's own cards and the Training rail both write,
                // so arriving from here is the same event as arriving from there.
                frappe.set_route("learn", node.getAttribute("data-course"));
            });
        });
    }

    function load(container) {
        const body = container.querySelector("#tmd-body");
        frappe
            .call({ method: METHOD })
            .then((r) => render(container, (r && r.message) || {}))
            .catch(() => {
                // frappe has already shown the server's message. Leave a line behind
                // so the widget is not silently blank.
                body.innerHTML = '<div class="tmd-muted">Could not load your training.</div>';
            });
    }

    function startApp(container) {
        const refresh = container.querySelector("#tmd-refresh");
        if (refresh) refresh.addEventListener("click", () => load(container));
        load(container);
    }

    waitForDOM();
})();
