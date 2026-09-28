// Build WIP & Aging — Production Dashboard Custom HTML Block.
//
// Active builds, longest-running first, from
// erpnext_enhancements.api.production_dashboard.get_wip_aging.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then. Nothing is cached across renders, and the refresh listener is bound to the
// fresh DOM each time.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.production_dashboard.get_wip_aging";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#pwa-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function esc(value) {
        return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
    }

    function money(value) {
        if (value === null || value === undefined || value === "") return "";
        try {
            return frappe.format(value, { fieldtype: "Currency" });
        } catch (e) {
            return String(value);
        }
    }

    function num(value, decimals) {
        if (value === null || value === undefined || value === "") return "—";
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals || 0,
            maximumFractionDigits: decimals || 0,
        });
    }

    function muted(text) {
        return `<div class="pwa-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#pwa-body");
        const count = container.querySelector("#pwa-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Build WIP & Aging is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const projects = message.projects || [];
        count.textContent = projects.length ? __("{0} in flight", [projects.length]) : "";
        if (!projects.length) {
            body.innerHTML = muted(__("No build is in progress."));
            return;
        }

        body.innerHTML = projects
            .map((p) => {
                const pct = Math.max(0, Math.min(100, Number(p.percent) || 0));
                const fill = pct >= 80 ? "pwa-fill-good" : pct >= 40 ? "pwa-fill-warn" : "";
                const late = p.overdue_days
                    ? `<span class="pwa-pill pwa-pill-bad">${esc(__("{0}d late", [p.overdue_days]))}</span>`
                    : "";
                const meta = [p.customer, money(p.amount)].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="pwa-row">
                        <a class="pwa-name" href="/app/project/${encodeURIComponent(p.name)}">${esc(p.title)}</a>
                        ${late}
                        <span class="pwa-meta">${meta}</span>
                        <span class="pwa-bar" title="${esc(pct + "%")}"><span class="pwa-fill ${fill}" style="width:${pct}%"></span></span>
                        <span class="pwa-age">${esc(__("{0}d open", [p.age_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#pwa-body");
        const refresh = container.querySelector("#pwa-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket and only the newest one draws. The
        // ticket lives on the root: an answer only ever draws into the root it was for.
        function load() {
            const ticket = (container.__ee_ticket = (container.__ee_ticket || 0) + 1);
            const current = () => container.__ee_ticket === ticket;
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(container, r.message);
                })
                .catch(() => {
                    if (current()) body.innerHTML = muted(__("Could not load builds in progress."));
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
        // Coming back to this workspace runs nothing again (the header), so the shared
        // helper reloads the block then. A bundle cached from before the helper has no
        // helper, and the block still draws; it just keeps its first answer.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
