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
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a course, a
// form or a list does not (v16's Workspace.show() returns early on the workspace
// already shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again then:
// the course you just finished leaves "Required" when you come back. Nothing is
// cached across renders and every listener is bound to the fresh DOM each time.
// Same model as the Finance widgets and hr_training_compliance.
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
    // sf_kit (panels, chips, escaping), set by startApp before anything draws.
    let sf = null;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    // The kit (panels, chips, escaping) and the ticket come from the shared helper,
    // workspace_block_return.js. A bundle cached from before them has neither: the block
    // then draws plain text, and every answer draws.
    function withKit(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.kit && blocks.ticket) {
            blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
            return;
        }
        const words = {
            loading: __("Loading…"),
            empty: __("Nothing to show"),
            error: __("Could not load"),
            "no-access": __("No access"),
        };
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || words[kind] || "");
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#tmd-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
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
                    sf.esc(t.value) +
                    '</div><div class="tmd-tile-label">' +
                    sf.esc(t.label) +
                    "</div></div>"
            )
            .join("");
    }

    function courseRow(row) {
        const bits = [];
        if (row.overdue) {
            bits.push(sf.chip(__("Overdue"), "danger"));
        } else if (row.due_date) {
            bits.push("<span>Due " + sf.esc(frappe.datetime.str_to_user(row.due_date)) + "</span>");
        } else if (row.status) {
            bits.push("<span>" + sf.esc(row.status) + "</span>");
        }
        if (row.minutes) bits.push("<span>" + sf.esc(row.minutes) + " min</span>");

        const done = pct(row.percent_complete);
        const bar = done > 0
            ? '<span class="tmd-bar"><span class="tmd-bar-fill" style="width:' + done + '%"></span></span>'
            : "";
        return (
            '<button type="button" class="tmd-row" data-course="' +
            sf.esc(row.course) +
            '"><span class="tmd-row-title">' +
            sf.esc(row.title) +
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
            : '<div class="tmd-muted">' + sf.esc(emptyText) + "</div>";
        return '<div class="tmd-section"><div class="tmd-section-title">' + sf.esc(title) + "</div>" + body + "</div>";
    }

    function render(container, data) {
        const body = container.querySelector("#tmd-body");
        const sub = container.querySelector("#tmd-sub");

        if (data.enabled === false) {
            // The server's sentence, not a wall of zeros. "You have no training" is
            // a statement about this person and would be wrong.
            sub.textContent = "";
            container.querySelector("#tmd-stats").innerHTML = "";
            sf.state.render(body, "empty", { title: data.message || __("Training is switched off.") });
            return;
        }

        renderTiles(container, data);
        const required = data.required || [];
        const optional = data.optional || [];
        const completed = data.completed || [];
        sub.textContent = required.length ? required.length + " required outstanding" : "nothing outstanding";

        if (!required.length && !optional.length && !completed.length) {
            sf.state.render(body, "empty", { title: __("Nothing required of you right now.") });
            return;
        }

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

    function startApp(container, kit, ticket) {
        sf = kit;
        const body = container.querySelector("#tmd-body");
        const refresh = container.querySelector("#tmd-refresh");

        // The refresh button and a return to the workspace can both ask while an
        // answer is on its way, so each ask takes a ticket on the root (blocks.ticket)
        // and only the newest one draws. Still the viewer's own record: the endpoint
        // reads the session user, and nothing here names anybody.
        function load() {
            const current = ticket(container);
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(container, (r && r.message) || {});
                })
                .catch((err) => {
                    // frappe has already shown the server's message. Leave a panel behind
                    // so the widget is not silently blank.
                    if (!current()) return;
                    const title = __("Could not load your training.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                });
        }

        if (refresh) refresh.addEventListener("click", load);
        sf.state.render(body, "loading");
        load();
        // Coming back to My Training runs nothing again (the header): the shared helper
        // calls load() then. A bundle cached from before the helper has none,
        // and the block still draws; it just keeps its first answer.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
