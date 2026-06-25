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
// model as the Morning Briefing / Task Dashboard blocks).

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#kpi-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("KPI Cockpit: block DOM never appeared.");
        }
    }

    function fmtTrend(pct) {
        if (pct === null || pct === undefined) return { cls: "flat", text: "" };
        const rounded = Math.round(pct * 10) / 10;
        if (rounded > 0) return { cls: "up", text: `▲ ${rounded}%` };
        if (rounded < 0) return { cls: "down", text: `▼ ${Math.abs(rounded)}%` };
        return { cls: "flat", text: "0%" };
    }

    function renderCards(body, meta, snap) {
        if (!snap) {
            body.innerHTML = `<div class="kpi-disabled">${__("No snapshot yet — it generates overnight, or press Refresh.")}</div>`;
            meta.textContent = "";
            return;
        }
        const esc = frappe.utils.escape_html;
        const cards = (snap.values || [])
            .map((v) => {
                const status = (v.status || "").toLowerCase();
                const statusClass = status ? `status-${status}` : "";
                const trend = fmtTrend(v.trend_pct);
                const target =
                    v.target_value !== null && v.target_value !== undefined && v.target_value !== 0
                        ? `<span class="kpi-target">${__("Target")}: ${esc(String(v.target_value))}</span>`
                        : "<span></span>";
                const stale = v.is_stale ? `<span class="kpi-stale">${__("stale source")}</span>` : "";
                return `
                    <div class="kpi-card ${statusClass}">
                        <div class="kpi-card-label">${esc(v.label || v.kpi_key)}</div>
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
        body.innerHTML = cards
            ? `<div class="kpi-grid">${cards}</div>`
            : `<div class="kpi-disabled">${__("This snapshot has no KPI values.")}</div>`;
        const gen = snap.generated_at ? ` · ${snap.generated_at}` : "";
        meta.textContent = `${snap.snapshot_date || ""}${gen}`;
    }

    function startApp(container) {
        const state = (window.__ee_kpi_cockpit = window.__ee_kpi_cockpit || {});

        const body = container.querySelector("#kpi-body");
        const meta = container.querySelector("#kpi-meta");
        const select = container.querySelector("#kpi-dept");
        const refresh_btn = container.querySelector("#kpi-refresh");

        function showMessage(message) {
            body.innerHTML = `<div class="kpi-disabled">${frappe.utils.escape_html(message)}</div>`;
            meta.textContent = "";
        }

        function load(force) {
            const dept = select.value;
            if (!dept) {
                showMessage(__("No KPI dashboards are available for your role."));
                return;
            }
            body.innerHTML = `<div class="kpi-loading">${force ? __("Recomputing…") : __("Loading…")}</div>`;
            refresh_btn.disabled = true;
            const method = force
                ? "erpnext_enhancements.api.kpi.refresh_kpi_dashboard"
                : "erpnext_enhancements.api.kpi.get_kpi_dashboard";
            frappe
                .call({ method, args: { department: dept } })
                .then((r) => {
                    const m = r.message || {};
                    if (!m.available) {
                        showMessage(m.reason || __("KPI dashboard unavailable."));
                        return;
                    }
                    renderCards(body, meta, m.snapshot);
                })
                .catch((err) => {
                    console.error("KPI Cockpit load failed:", err);
                    showMessage(__("Could not load the KPI dashboard."));
                })
                .then(() => {
                    refresh_btn.disabled = false;
                });
        }

        state.load = load;
        select.addEventListener("change", () => load(false));
        refresh_btn.addEventListener("click", () => load(true));

        // Populate the department selector (role-gated), then load the first one.
        frappe
            .call({ method: "erpnext_enhancements.api.kpi.visible_departments" })
            .then((r) => {
                const depts = r.message || [];
                if (!depts.length) {
                    select.innerHTML = "";
                    showMessage(__("No KPI dashboards are available for your role."));
                    return;
                }
                select.innerHTML = depts
                    .map((d) => `<option value="${frappe.utils.escape_html(d)}">${frappe.utils.escape_html(d)}</option>`)
                    .join("");
                load(false);
            })
            .catch((err) => {
                console.error("KPI Cockpit: could not list departments:", err);
                showMessage(__("Could not load KPI dashboards."));
            });
    }

    waitForDOM();
})();
