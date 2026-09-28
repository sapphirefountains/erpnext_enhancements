// Renewal Radar — Sales Dashboard Custom HTML Block.
//
// Active maintenance contracts ending inside the renewal horizon, soonest first,
// from erpnext_enhancements.api.sales_dashboard.get_renewal_radar.
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
    const METHOD = "erpnext_enhancements.api.sales_dashboard.get_renewal_radar";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#srr-body")) {
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
        return `<div class="srr-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#srr-body");
        const count = container.querySelector("#srr-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Renewal Radar is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const contracts = message.contracts || [];
        count.textContent = contracts.length
            ? __("{0} in {1}d", [contracts.length, message.horizon_days])
            : "";
        if (!contracts.length) {
            body.innerHTML = muted(__("No contract ends in the next {0} days.", [message.horizon_days]));
            return;
        }

        body.innerHTML = contracts
            .map((c) => {
                // A non-renewal notice outranks auto-renew: it is the one flag that
                // means this contract ends unless somebody acts.
                const flag = c.non_renewing
                    ? `<span class="srr-pill srr-pill-bad">${__("Non-renewing")}</span>`
                    : c.auto_renew
                      ? `<span class="srr-pill srr-pill-good">${__("Auto-renew")}</span>`
                      : "";
                const meta = [c.customer, money(c.amount)].filter(Boolean).map(esc).join(" · ");
                const tone = c.days_left <= 30 ? "srr-bad" : c.days_left <= 60 ? "srr-warn" : "";
                return `
                    <div class="srr-row">
                        <a class="srr-name" href="/app/sapphire-maintenance-contract/${encodeURIComponent(c.name)}">${esc(c.name)}</a>
                        ${flag}
                        <span class="srr-meta">${meta}</span>
                        <span class="srr-age ${tone}">${esc(__("{0}d left", [c.days_left]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#srr-body");
        const refresh = container.querySelector("#srr-refresh");

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
                    if (current()) body.innerHTML = muted(__("Could not load upcoming renewals."));
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
