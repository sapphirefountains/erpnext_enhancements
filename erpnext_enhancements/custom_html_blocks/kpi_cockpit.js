// KPI Cockpit — Custom HTML Block script (runs in the block's shadow-DOM
// sandbox; `root_element` is the shadow root provided by Frappe).
//
// Populates the department selector from
// erpnext_enhancements.api.kpi.visible_departments (role-gated), then renders
// the latest precomputed KPI Snapshot for the chosen department from
// get_kpi_dashboard. Refresh calls refresh_kpi_dashboard (recompute now).
//
// Workspace re-renders re-run this whole script with a NEW root_element, so
// state lives on `window` and listeners are re-bound to the fresh DOM (same
// model as the Morning Briefing / Task Dashboard blocks). A re-render is the
// first visit and coming back from a different workspace, not every return: from
// a form or a list, v16's Workspace.show() returns early on the workspace already
// shown. Once the departments are listed, startApp hands a snapshot read to the
// shared return helper, public/js/global_enhancements/workspace_block_return.js,
// which runs it again then.
//
// Its panels (loading, empty, error, no access) and its escaping come from sf_kit, and
// its "only the newest answer draws" counter from blocks.ticket, both handed over by the
// same helper (custom_html_blocks/README.md).

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

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
        if (container.querySelector("#kpi-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("KPI Cockpit: block DOM never appeared.");
        }
    }

    // Each department dashboard (where the seeder also places this block) maps
    // 1:1 to a KPI department. When the cockpit sits on one, lock to that
    // department instead of showing the picker. Detection is by route first
    // (slug or title form), with a page-title fallback — so it degrades to the
    // picker on Home, the "KPI Dashboards" workspace, or any unmatched route.
    const DASHBOARD_DEPARTMENTS = {
        "finance dashboard": "Finance",
        "sales dashboard": "Sales",
        "operations dashboard": "Operations",
        "design dashboard": "Design",
        "production dashboard": "Production",
        "service dashboard": "Service",
        "marketing dashboard": "Marketing",
        "product dashboard": "Product",
        "hr dashboard": "HR",
        "executive dashboard": "Executive",
    };

    function normName(s) {
        return String(s || "")
            .toLowerCase()
            .replace(/[-_]+/g, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function detectLockedDepartment() {
        // 1) Current route — e.g. ["finance-dashboard"] or ["Workspaces","Finance Dashboard"].
        try {
            const route = (frappe.get_route && frappe.get_route()) || [];
            for (const seg of route) {
                let decoded = seg;
                try {
                    decoded = decodeURIComponent(seg);
                } catch (e) {
                    /* malformed % escape — use the raw segment */
                }
                const dept = DASHBOARD_DEPARTMENTS[normName(decoded)];
                if (dept) return dept;
            }
        } catch (e) {
            /* fall through to the title check */
        }
        // 2) The workspace title shown in the page head.
        try {
            const el = document.querySelector(".workspace-title, .page-title .title-text, .title-text");
            const dept = el && DASHBOARD_DEPARTMENTS[normName(el.textContent)];
            if (dept) return dept;
        } catch (e) {
            /* fall through */
        }
        return null;
    }

    function fmtTrend(pct) {
        if (pct === null || pct === undefined) return { cls: "flat", text: "" };
        const rounded = Math.round(pct * 10) / 10;
        if (rounded > 0) return { cls: "up", text: `▲ ${rounded}%` };
        if (rounded < 0) return { cls: "down", text: `▼ ${Math.abs(rounded)}%` };
        // 0 == flat OR no prior snapshot (Float can't be null) — show nothing.
        return { cls: "flat", text: "" };
    }

    function renderCards(body, meta, snap, sf) {
        if (!snap) {
            sf.state.render(body, "empty", {
                title: __("No snapshot yet — it generates overnight, or press Refresh."),
            });
            meta.textContent = "";
            return;
        }
        // The kit's escape, under the name the card markup below reads it by.
        const { esc } = sf;
        const cards = (snap.values || [])
            .map((v) => {
                const status = (v.status || "").toLowerCase();
                const statusClass = status ? `status-${status}` : "";
                const trend = fmtTrend(v.trend_pct);
                const target =
                    // A zero target is shown when it grades (e.g. "0 unpriced PO lines"); an
                    // ungraded 0 is how an unset target has always come through.
                    v.target_value !== null && v.target_value !== undefined && (v.target_value !== 0 || status)
                        ? `<span class="kpi-target">${__("Target")}: ${esc(String(v.target_value))}</span>`
                        : "<span></span>";
                const stale = v.is_stale ? `<span class="kpi-stale">${__("stale source")}</span>` : "";
                // The left border's colour is the grade; the word says it too, for anyone who
                // can't tell the colours apart or can't see them (S3.6).
                const word = { good: __("Good"), watch: __("Watch"), bad: __("Bad") }[status] || v.status || "";
                const grade = word ? `<span class="kpi-status">${esc(word)}</span>` : "";
                return `
                    <div class="kpi-card ${statusClass}">
                        <div class="kpi-card-label">${grade}${esc(v.label || v.kpi_key)}</div>
                        <div>
                            <div class="kpi-card-value">${esc(v.value_text || String(v.value))}</div>
                            ${stale}
                        </div>
                        <div class="kpi-card-foot">
                            ${target}
                            <span class="kpi-trend ${trend.cls}">${trend.text}</span>
                        </div>
                    </div>`;
            })
            .join("");
        if (cards) body.innerHTML = `<div class="kpi-grid">${cards}</div>`;
        else sf.state.render(body, "empty", { title: __("This snapshot has no KPI values.") });
        const gen = snap.generated_at ? ` · ${snap.generated_at}` : "";
        meta.textContent = `${snap.snapshot_date || ""}${gen}`;
    }

    function startApp(container, sf, ticket) {
        const state = (window.__ee_kpi_cockpit = window.__ee_kpi_cockpit || {});

        const body = container.querySelector("#kpi-body");
        const meta = container.querySelector("#kpi-meta");
        const select = container.querySelector("#kpi-dept");
        const refresh_btn = container.querySelector("#kpi-refresh");
        const title = container.querySelector(".kpi-title");

        // Recomputed each render. The workspace renders the page again, and runs this
        // script with the route of the moment, whenever you move from one workspace to
        // another (one department dashboard to the next included), so this tracks which
        // dashboard we're on now. A return to the same dashboard keeps its department.
        const locked = detectLockedDepartment();

        // Nothing to show (no department for this role, KPIs switched off, none configured):
        // the server's own sentence in the kit's empty panel.
        function showEmpty(message) {
            sf.state.render(body, "empty", { title: message });
            meta.textContent = "";
        }

        // A failed call: no access on a 403, otherwise the error panel, whose Try again
        // `retry` runs (a snapshot read, never a recompute).
        function showFailure(err, title, retry) {
            meta.textContent = "";
            if (err && err.status === 403) sf.state.render(body, "no-access");
            else sf.state.render(body, "error", { title, on_action: retry });
        }

        // Picking another department while an answer is on its way means two asks at
        // once, so each takes a ticket on the root (blocks.ticket) and only the newest one
        // draws: an answer for the department you just left never lands under the one you
        // picked.
        function load(force) {
            const dept = select.value;
            if (!dept) {
                showEmpty(__("No KPI dashboards are available for your role."));
                return;
            }
            const current = ticket(container);
            sf.state.render(body, "loading", force ? { title: __("Recomputing…") } : {});
            refresh_btn.disabled = true;
            const method = force
                ? "erpnext_enhancements.api.kpi.refresh_kpi_dashboard"
                : "erpnext_enhancements.api.kpi.get_kpi_dashboard";
            frappe
                .call({ method, args: { department: dept } })
                .then((r) => {
                    if (!current()) return;
                    const m = r.message || {};
                    if (!m.available) {
                        showEmpty(m.reason || __("KPI dashboard unavailable."));
                        return;
                    }
                    renderCards(body, meta, m.snapshot, sf);
                })
                .catch((err) => {
                    if (!current()) return;
                    console.error("KPI Cockpit load failed:", err);
                    const title = __("Could not load the KPI dashboard.");
                    showFailure(err, title, () => load(false));
                })
                .then(() => {
                    if (current()) refresh_btn.disabled = false;
                });
        }

        state.load = load;
        select.addEventListener("change", () => load(false));
        refresh_btn.addEventListener("click", () => load(true));

        // Populate the department selector (role-gated), then load the first one. Its
        // Try again lists them again.
        function listDepartments() {
            sf.state.render(body, "loading");
            frappe
                .call({ method: "erpnext_enhancements.api.kpi.visible_departments" })
                .then((r) => {
                    const depts = Array.isArray(r.message) ? r.message : [];
                    if (!depts.length) {
                        select.innerHTML = "";
                        showEmpty(__("No KPI dashboards are available for your role."));
                        return;
                    }
                    showDepartments(depts);
                })
                .catch((err) => {
                    console.error("KPI Cockpit: could not list departments:", err);
                    const title = __("Could not load KPI dashboards.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: listDepartments });
                });
        }

        function showDepartments(depts) {
            select.innerHTML = depts
                .map((d) => `<option value="${sf.esc(d)}">${sf.esc(d)}</option>`)
                .join("");

            // Lock to one department when this block sits on that
            // department's dashboard (by route), or when the user can see
            // exactly one. Otherwise keep the picker (Home / overview).
            let forced = locked && depts.includes(locked) ? locked : null;
            if (!forced && depts.length === 1) forced = depts[0];

            if (forced) {
                select.value = forced;
                select.style.display = "none";
                if (title) title.textContent = __("{0} KPIs", [forced]);
            } else {
                select.style.display = "";
                if (title) title.textContent = __("KPI Dashboard");
            }
            load(false);

            // Registered only now: before the list arrives there is no department to
            // read. A return reads the snapshot, never recomputes, and waits out an
            // answer already on its way (a Refresh recompute must be the one that
            // draws); the button is disabled exactly while one is.
            const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
            if (blocks && blocks.onWorkspaceReturn) {
                blocks.onWorkspaceReturn(container, () => {
                    if (!refresh_btn.disabled) load(false);
                });
            }
        }

        listDepartments();
    }

    waitForDOM();
})();
